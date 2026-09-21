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


## Already-declared addresses and model shape normalization

Known addresses do not need an address-confirmation question just to acquire a
purpose label. `analysis_results` accepts both grouped `suggested_io` mappings
(`{"X":{"X0":"启动按钮"}}`) and flat address-to-string mappings
(`{"X0":"启动按钮"}`), normalizes aliases, and applies the same CPU/address checks.
A complete device key is not classified as a hardware category. Arbitrary
module/channel dictionaries are not recursively flattened into I/O.

Explicit user declarations also seed the review I/O table when the model omits
it. The preservation grammar accepts `address: purpose`, `address 为/是 purpose`,
and `address is purpose` at line/semicolon/Chinese-sentence boundaries. For
example, `X0 为启动按钮，按下时 ON；X1 为停止按钮，按下时 ON；Y0 为运行输出。`
creates the three declared rows. The physical-level suffix remains in original
`intent_context.requests`, not in the short comment. This is a bounded input
adapter, not arbitrary natural-language interpretation or address-based guessing;
questions, state conditions, numeric classification rules and instruction operands
are not used as names. Existing user-edited/cleared labels, moved rows and explicit
deletions still take precedence on reanalysis.

The confirmation-to-export sentinel starts from that paragraph and a synthetic
flat analysis response, with no prefilled I/O table. It checks the review draft,
ConfirmedSpec v4, actual Agent B request, saved ladder/IR, explorer response, SVG
and UTF-16 comment CSV. The captured report contains a generation transcript,
not Agent A's original completion; the flat fixture reproduces the observed
empty table plus `hardware_context.x0/x1/y0`, rather than claiming to be its raw
response. No annotation-only model call is added.

Saved versions are immutable. Updating the application does not rewrite the
previously empty I/O table or silently change its old artifacts. For a clean
regression, start a new project with the same declaration paragraph. An existing
confirmed empty allocation can be explicitly corrected in the I/O editor before
generation; reanalysis must not silently resurrect removed allocations.
