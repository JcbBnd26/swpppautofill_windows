import argparse
import json
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

from app.core.config_manager import load_mapping
from app.core.pdf_fields import (build_audit_mapping_document,
                                 extract_checkbox_rows)


def _serialize_field(field):
    states = field.get("/_States_") or []
    return {
        "value": getattr(field, "value", None) if hasattr(field, "value") else None,
        "type": str(field.get("/FT")) if hasattr(field, "get") else None,
        "kids": len(field.get("/Kids", [])) if hasattr(field, "get") else 0,
        "states": [str(state) for state in states],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.tools.inspect")
    parser.add_argument("pdf_path")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--checkbox-rows", action="store_true")
    parser.add_argument(
        "--export-checkbox-mapping",
        nargs="?",
        const="-",
        metavar="OUTPUT_PATH",
        help="write an audited YAML mapping with inferred checkbox targets; omit OUTPUT_PATH to print to stdout",
    )
    parser.add_argument(
        "--config",
        default="app/core/config_example.yaml",
        help="mapping YAML to augment when exporting checkbox targets",
    )
    return parser.parse_args(argv)


def _write_export_document(output_path: str, document: dict) -> None:
    rendered = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
    if output_path == "-":
        sys.stdout.write(rendered)
        return

    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote audited mapping to {target_path}")

def main():
    args = _parse_args(sys.argv[1:])
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(2)

    if args.export_checkbox_mapping is not None:
        mapping = load_mapping(Path(args.config))
        document = build_audit_mapping_document(mapping, pdf_path)
        _write_export_document(args.export_checkbox_mapping, document)
        return

    if args.checkbox_rows:
        out = [
            {
                "page": row.page,
                "y": row.y,
                "yes": row.yes.__dict__ if row.yes else None,
                "no": row.no.__dict__ if row.no else None,
                "na": row.na.__dict__ if row.na else None,
            }
            for row in extract_checkbox_rows(pdf_path)
        ]
    else:
        reader = PdfReader(str(pdf_path))
        fields = reader.get_fields() or {}
        if args.details:
            out = {k: _serialize_field(v) for k, v in fields.items()}
        else:
            out = {k: getattr(v, "value", None) if hasattr(v, "value") else None for k, v in fields.items()}
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
