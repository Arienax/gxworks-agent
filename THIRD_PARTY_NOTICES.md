# Third-party notices

Project-authored public interface files use MPL-2.0. The proprietary Runtime
uses `LICENSE-RUNTIME.md`. Neither license replaces third-party terms.

## Frontend and npm launcher

Dependency versions and integrity records are pinned in `web/package-lock.json`
and the public repository's root `package-lock.json`. Third-party packages
retain their own licenses and copyright notices. Redistributed builds must
retain the applicable license texts and notices supplied with those packages.

The interface uses React, Radix UI, Lucide icons, clsx, class-variance-authority
and tailwind-merge. Build tooling includes Vite, TypeScript, Tailwind CSS and
openapi-typescript. The npm launcher uses yauzl. Consult the installed versions'
license texts for the applicable terms.

## Official runtime packages

Official packages include `THIRD_PARTY_LICENSES/` with dependency version
records and collected license texts, including applicable Python, native
helper and frontend dependencies. That directory supplements this notice;
it is not a replacement for an individual component's license.

Knowledge-source notices shipped with the Runtime are in
`knowledge/THIRD_PARTY_NOTICES.md`. In the private development tree, the
original notice is `resources/knowledge/THIRD_PARTY_NOTICES.md`. Preserve that
notice unchanged with the relevant material. The public source repository
does not contain private knowledge databases, vendor documents or native
binaries.

## Vendor names and rights

GX Works, MELSEC and Mitsubishi Electric names belong to their respective
owners. This project is independent of Mitsubishi Electric. Project licenses
do not grant rights to vendor software, manuals, trademarks or engineering
materials; obtain any necessary third-party permissions separately.
