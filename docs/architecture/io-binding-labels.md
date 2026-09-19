# I/O purpose labels

An address question and a device purpose are separate fields. Analysis may attach
optional `io_binding.label` to an existing address question:

```json
{
  "id": "start_device",
  "question": "启动按钮接在哪个输入点，程序中使用何种触点极性？",
  "required": true,
  "options": ["X000", "自定义"],
  "io_binding": {
    "binding_id": "machine.start",
    "kind": "X",
    "label": "启动按钮"
  }
}
```

`binding_id` remains the stable identity, and `kind` identifies the address
category. `role` and `row_id` keep their existing optional meanings. A purpose
label is not an address, a polarity answer, a proposed architecture, or a reason
to create another required question. Missing/invalid optional labels do not
trigger a model repair or prevent confirmation.

## Data path

The shared analysis assembler supplies this metadata contract in both Direct
and Design, including a selected Direct baseline. `binding_hint` retains the
string label through question normalization and choice merging. Confirmation
binds the actual user answer and seeds a *new* I/O row from that independent
label. It never creates a comment by deleting words from a question.

Existing I/O labels, including an explicitly cleared label, remain authoritative.
After a row edit, its canonical label follows the active `io_bindings` record
and retained compound address/polarity metadata. Reanalysis cannot overwrite it
with a fresh model suggestion. Distinct identities stay distinct even when their
question text is identical. Deleting a row does not resurrect its old answer.

The shared confirmed-generation projection carries the allowlisted scalar label
alongside the binding, while the existing compact expansion obtains device
comments from `io_table.label`. IR, SVG and GX Works2 comment CSV use that same
purpose text. No new model call, response-schema gate, frontend domain logic,
protocol grammar, polarity inference, or native operation is introduced.

## Older records

Known legacy start/stop/output binding roles may use a neutral short purpose
when no independent name exists. Arbitrary questions with neither a label nor a
known role keep an empty purpose; the question is still retained as provenance.
The old question-to-label helper is used only to match historical unbound rows,
not to create comments for new rows.

An existing nonempty label is not automatically rewritten, even when it resembles
an old generated question. Older records do not reliably distinguish that case
from a user-authored label. Existing projects can correct the I/O purpose in the
specification editor; saved historical program versions remain unchanged.

## Regression coverage

`tests/test_spec_choice_metadata.py` covers analysis metadata, unresolved values,
confirmation and rebinding, multiple devices, manual renaming/clearing, stale
suggestions, malformed optional labels, public projection, and both prompt modes.
An offline HTTP-to-artifact regression saves and edits the specification without
a model call, then uses one synthetic generation response containing logic only.
It checks the saved ladder/IR, SVG and comment CSV for the independent names.
