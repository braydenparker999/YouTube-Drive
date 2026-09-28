#!/usr/bin/env bash
# Shared by the archive and its real-network proof. No Google/GitHub secrets needed.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true ]]; then
  echo 'This installer is for disposable GitHub Actions runners only.' >&2
  exit 2
fi
case "${YTDRIVE_EGRESS:-warp}" in
  warp|direct|custom) ;;
  *) echo 'YTDRIVE_EGRESS must be warp, direct, or custom.' >&2; exit 2 ;;
esac
sudo apt-get update -qq
sudo apt-get install -y ffmpeg
npm install --global deno@2.9.6
python scripts/install_ytdlp.py
python -c 'from src.downloader import require_media_tools; print(require_media_tools())'
if [[ "${YTDRIVE_EGRESS:-warp}" == warp ]]; then
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list
  sudo apt-get update -qq
  sudo apt-get install -y cloudflare-warp
  sudo systemctl start warp-svc
  python scripts/setup_warp.py
fi
# Both services bind loopback. Host networking lets the container reach WARP's local proxy.
PO_PROVIDER_HOST_NETWORK=1 python scripts/setup_po_provider.py
