# Contributing

Project-authored files are offered under [Apache-2.0](LICENSE), unless a file
states another license. Contributors retain their copyright and must have the
right to submit their changes under the target file's terms. Keep existing
copyright and third-party notices. No copyright assignment is required.

Do not submit credentials, customer projects, Mitsubishi manuals or binaries,
derived manual databases, local reverse-engineering evidence, or internal
development records. Sanitize diagnostics before attaching them to issues.

For a code change, run the checks relevant to the changed modules and report
what was actually checked. Frontend changes: `npm --prefix web ci`,
`npm --prefix web test`, and `npm --prefix web run build`. Python changes:
install the relevant requirements and run focused tests. Native GX Works2
acceptance and PLC behavior require separate evidence; a unit test does not
establish either.

Contributions to the public repository must also pass
`python tools/check_public_boundary.py`. Update the explicit file manifest when
adding a new public source file. Source data omitted for third-party rights or
privacy must stay out of commits and build artifacts.
