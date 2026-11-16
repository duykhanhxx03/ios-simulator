#!/bin/bash
# Install blacktop/ipsw (MIT) CLI into WSL, pinned release.
set -euo pipefail
VER=3.1.745
cd /opt/src
curl -fSL -o ipsw.tgz "https://github.com/blacktop/ipsw/releases/download/v${VER}/ipsw_${VER}_linux_x86_64.tar.gz"
rm -rf ipsw && mkdir ipsw && tar xzf ipsw.tgz -C ipsw
install -m755 ipsw/ipsw /usr/local/bin/ipsw
ipsw version | head -3
