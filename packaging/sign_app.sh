#!/usr/bin/env bash
# Signs the app with a stable certificate, so macOS keeps its permissions (Microphone, Accessibility,
# Input Monitoring) across updates: macOS ties them to the signing certificate, not to the file.
#
# Uses $SIGNING_CERTIFICATE_PEM (private key + certificate, PEM), e.g. from the GitHub secret of the
# same name. Without it, signs with a throwaway certificate: fine for testing, but then every build
# looks like a different app to macOS.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="$1"
WORK=build/signing
mkdir -p "$WORK"
PEM="$WORK/signing.pem"
trap 'rm -f "$PEM" "$WORK/key.pem"' EXIT

if [[ -n "${SIGNING_CERTIFICATE_PEM:-}" ]]; then
  printf '%s\n' "$SIGNING_CERTIFICATE_PEM" > "$PEM"
  echo "Signing with the project's certificate"
else
  echo "::warning::SIGNING_CERTIFICATE_PEM is not set: signing with a throwaway certificate (permissions won't survive updates)"
  openssl req -x509 -newkey rsa:2048 -sha256 -days 30 -nodes -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
    -subj "/CN=Speech to Text (throwaway)" -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
  cat "$WORK/key.pem" "$WORK/cert.pem" > "$PEM"
fi
chmod 600 "$PEM"

# rcodesign signs from plain PEM files: no keychain, no Apple account.
RCODESIGN=$(command -v rcodesign || true)
if [[ -z "$RCODESIGN" ]]; then
  TAG=$(gh release list -R indygreg/apple-platform-rs --limit 100 --json tagName \
    --jq '[.[] | select(.tagName | startswith("apple-codesign/"))][0].tagName')
  rm -rf "$WORK/rcodesign" && mkdir -p "$WORK/rcodesign"
  gh release download "$TAG" -R indygreg/apple-platform-rs --pattern '*macos-universal.tar.gz' --dir "$WORK/rcodesign"
  tar -xzf "$WORK"/rcodesign/*.tar.gz -C "$WORK/rcodesign"
  RCODESIGN=$(find "$WORK/rcodesign" -type f -name rcodesign | head -1)
fi
"$RCODESIGN" --version

if "$RCODESIGN" sign --help | grep -q -- '--pem-file'; then PEM_FLAG=--pem-file; else PEM_FLAG=--pem-source; fi
"$RCODESIGN" sign "$PEM_FLAG" "$PEM" "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d -r- "$APP" 2>&1 | grep "designated =>"
