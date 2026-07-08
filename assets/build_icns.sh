#!/bin/sh
# build_icns.sh — 由 icon_1024.png 產出 PTTBar.icns（先跑 make_icon.py 產生母圖）
set -e
cd "$(dirname "$0")"
rm -rf PTTBar.iconset
mkdir PTTBar.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s icon_1024.png --out "PTTBar.iconset/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z $d $d icon_1024.png --out "PTTBar.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns PTTBar.iconset -o PTTBar.icns
rm -rf PTTBar.iconset
echo "wrote assets/PTTBar.icns"
