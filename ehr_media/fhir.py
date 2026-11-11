"""FHIR R4 resources, checked with R4B Python models AND the exact R4 schema."""
import base64
import copy
import json
import html
import uuid
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from fhir.resources.R4B.bundle import Bundle
from jsonschema import Draft6Validator
from pydantic import ValidationError

from .ingestion import stable_id
from .models import CleanRecord

ROOT = Path(__file__).resolve().parents[1]


def reference(resource_type: str, resource_id: str) -> str:
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://synthetic.example/{resource_type}/{resource_id}"))


def make_bundle(records: list[CleanRecord]) -> dict:
    if not records or len({r.patient.id for r in records}) != 1:
        raise ValueError("A bundle must contain exactly one patient's records")
    records = sorted(records, key=lambda r: (str(r.date or ""), r.id))
    person = records[0].patient
    patient = {"resourceType": "Patient", "id": person.id,
               "identifier": [{"system": "https://synthetic.example/mrn", "value": person.mrn}],
               "name": [{"text": person.name}], "gender": person.gender}
    if person.dob:
        patient["birthDate"] = person.dob.isoformat()
    resources = [patient]
    subject = {"reference": reference("Patient", person.id)}
    encounters = {}
    for record in records:
        encounter = None
        if record.encounter_id:
            eid = "e-" + stable_id(person.id, record.encounter_id)
            encounter = {"reference": reference("Encounter", eid)}
            # Neither a care setting nor a status is inferred from free text.
            encounters[eid] = {"resourceType": "Encounter", "id": eid, "status": "unknown",
                               "class": {"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/data-absent-reason", "valueCode": "unknown"}]},
                               "subject": subject}
        attachment = {"contentType": "text/plain; charset=utf-8", "title": record.title,
                      "data": base64.b64encode(record.text.encode()).decode()}
        if record.date:
            attachment["creation"] = record.date.isoformat()
        doc = {"resourceType": "DocumentReference", "id": record.id, "status": "current",
               "subject": subject, "type": {"text": record.kind.title()},
               "description": record.title, "content": [{"attachment": attachment}]}
        # R4 DocumentReference.date is an instant. A date-only source must not be
        # turned into a fabricated midnight timestamp; attachment.creation is dateTime.
        if encounter:
            doc["context"] = {"encounter": [encounter]}
        resources.append(doc)
        if record.kind in {"lab", "imaging"}:
            report = {"resourceType": "DiagnosticReport", "id": "dr-" + record.id,
                      "status": "unknown", "code": {"text": record.title}, "subject": subject,
                      "conclusion": record.text, "presentedForm": [copy.deepcopy(attachment)]}
            if record.date:
                report["effectiveDateTime"] = record.date.isoformat()
            if encounter:
                report["encounter"] = encounter
            resources.append(report)
    resources.extend(encounters.values())
    for resource in resources:
        header = f"{resource['resourceType']} for {person.name}, MRN {person.mrn}."
        lines = [header]
        if resource["resourceType"] == "Patient":
            lines.append(f"Administrative gender: {person.gender}. Date of birth: {person.dob or 'unknown'}.")
        elif resource["resourceType"] == "DocumentReference":
            attachment = resource["content"][0]["attachment"]
            lines.append(f"{attachment['title']}. Date: {attachment.get('creation', 'unknown')}. Document status: current.")
            lines.extend(base64.b64decode(attachment["data"]).decode().splitlines())
        elif resource["resourceType"] == "DiagnosticReport":
            lines.append(f"{resource['code']['text']}. Date: {resource.get('effectiveDateTime', 'unknown')}. Report status: unknown.")
            lines.extend(resource["conclusion"].splitlines())
        else:
            lines.append("Encounter class and status were not supplied in the source export.")
        resource["text"] = {"status": "generated", "div": '<div xmlns="http://www.w3.org/1999/xhtml">' +
                            "".join(f"<p>{html.escape(line)}</p>" for line in lines) + "</div>"}
    return {"resourceType": "Bundle", "id": "b-" + person.id, "type": "collection",
            "entry": [{"fullUrl": reference(r["resourceType"], r["id"]), "resource": r} for r in resources]}


@lru_cache(maxsize=1)
def r4_schema() -> dict:
    return json.loads((ROOT / "vendor/fhir-r4.schema.json").read_text())


def validate_bundle(bundle: dict) -> list[dict]:
    errors = []
    try:
        Bundle.model_validate(bundle)
    except ValidationError as error:
        errors.extend({"validator": "fhir.resources.R4B", "path": ".".join(map(str, e["loc"])),
                       "message": e["msg"]} for e in error.errors(include_url=False, include_input=False))
    # Direct per-resource validation avoids expensive ResourceList oneOf traversal
    # and produces useful field paths. The outer Bundle is validated independently.
    schema = r4_schema()
    for prefix, resource in [("Bundle", bundle)] + [
        (f"entry[{i}].resource", entry.get("resource", {})) for i, entry in enumerate(bundle.get("entry", []))
    ]:
        kind = resource.get("resourceType", "")
        if kind not in schema["definitions"]:
            errors.append({"validator": "HL7 R4", "path": prefix, "message": "Unknown resourceType"})
            continue
        local = {"$ref": f"#/definitions/{kind}", "definitions": schema["definitions"]}
        target = resource
        if kind == "Bundle":
            target = copy.deepcopy(resource)
            for entry in target.get("entry", []):
                entry.pop("resource", None)  # Each actual resource is checked separately above.
        for error in Draft6Validator(local).iter_errors(target):
            errors.append({"validator": "HL7 R4 4.0.1", "path": prefix + "." + ".".join(map(str, error.path)),
                           "message": error.message})
    entries = bundle.get("entry", [])
    urls = [entry.get("fullUrl") for entry in entries]
    if len(set(urls)) != len(urls) or None in urls:
        errors.append({"validator": "references", "path": "entry", "message": "fullUrl must exist and be unique"})
    patients = [e for e in entries if e.get("resource", {}).get("resourceType") == "Patient"]
    if len(patients) != 1:
        errors.append({"validator": "references", "path": "entry", "message": "Expected exactly one Patient"})
    patient_url = patients[0].get("fullUrl") if len(patients) == 1 else None
    by_url = {e.get("fullUrl"): e.get("resource", {}) for e in entries}

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                current = f"{path}.{key}"
                if key == "reference" and child not in by_url:
                    errors.append({"validator": "references", "path": current, "message": f"Unresolved reference: {child}"})
                else:
                    walk(child, current)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")
    walk(bundle)
    for i, entry in enumerate(entries):
        resource = entry.get("resource", {})
        if resource.get("resourceType") in {"DocumentReference", "DiagnosticReport", "Encounter"}:
            if resource.get("subject", {}).get("reference") != patient_url:
                errors.append({"validator": "references", "path": f"entry[{i}].subject", "message": "Subject must reference this bundle's Patient"})
            encounter_refs = resource.get("context", {}).get("encounter", [])
            if "encounter" in resource:
                encounter_refs = [resource["encounter"]]
            for ref in encounter_refs:
                if by_url.get(ref.get("reference"), {}).get("resourceType") != "Encounter":
                    errors.append({"validator": "references", "path": f"entry[{i}].encounter", "message": "Encounter reference has wrong resource type"})
    return errors


def make_bundles(records: list[CleanRecord]) -> list[dict]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record.patient.id].append(record)
    return [make_bundle(grouped[pid]) for pid in sorted(grouped)]


def document_sources(bundle: dict) -> list[dict]:
    sources = []
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] != "DocumentReference":
            continue
        attachment = resource["content"][0]["attachment"]
        sources.append({"id": resource["id"], "title": attachment.get("title", "Document"),
                        "date": attachment.get("creation"), "kind": resource["type"]["text"].lower(),
                        "text": base64.b64decode(attachment["data"], validate=True).decode("utf-8")})
    return sorted(sources, key=lambda s: (s["date"] or "", s["id"]), reverse=True)

