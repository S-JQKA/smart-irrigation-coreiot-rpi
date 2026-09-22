"""Refresh a tenant-exported widget type from the reviewed local sources."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'implementation/coreiot/widgets/field_selector'
DEFAULT_EXPORT = ROOT / 'deliverables/coreiot/irrigation_widgets/field_selector_widget_type.json'


def refresh_widget(input_export: Path, output: Path) -> None:
    doc = json.loads(input_export.read_text(encoding='utf-8-sig'))
    if not isinstance(doc, dict) or not isinstance(doc.get('descriptor'), dict):
        raise ValueError('Expected a widget-type export with an object descriptor')
    for key, filename in [('templateHtml', 'template.html'), ('templateCss', 'style.css'), ('controllerScript', 'controller.js')]:
        doc['descriptor'][key] = (SOURCE / filename).read_text(encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-export', type=Path, default=DEFAULT_EXPORT)
    parser.add_argument('--output', type=Path, help='Defaults to updating input in place')
    args = parser.parse_args()
    try:
        refresh_widget(args.input_export, args.output or args.input_export)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
