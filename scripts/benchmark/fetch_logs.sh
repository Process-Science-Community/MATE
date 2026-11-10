#!/bin/sh
# Download the four 4TU.ResearchData benchmark logs into DIR (default: ./bench-logs)
# under the names docs/benchmarks/ uses, and verify their published MD5 sums.
set -eu

dir=${1:-bench-logs}
mkdir -p "$dir"
cd "$dir"

fetch() { # url name md5 - download to a temp name, keep it only if the MD5 matches
  if [ -f "$2" ] && echo "$3  $2" | md5sum -c --quiet - 2>/dev/null; then
    echo "$2: OK (cached)"; return
  fi
  curl -fsSL --retry 4 -o "$2.part" "$1"
  echo "$3  $2.part" | md5sum -c --quiet -
  mv "$2.part" "$2"
  echo "$2: OK"
}

# Sepsis Cases - Event Log (doi:10.4121/uuid:915d2bfb-7e84-49ad-a286-dc35f063a460)
fetch https://data.4tu.nl/file/33632f3c-5c48-40cf-8d8f-2db57f5a6ce7/643dccf2-985a-459e-835c-a82bce1c0339 \
  sepsis.xes.gz b5671166ac71eb20680d3c74616c43d2
# Road Traffic Fine Management Process (doi:10.4121/uuid:270fd440-1057-4fb9-89a9-b699b47990f5)
fetch https://data.4tu.nl/file/806acd1a-2bf2-4e39-be21-69b8cad10909/b234b06c-4d4f-4055-9f14-6218e3906d82 \
  road_traffic.xes.gz dc4cae9a592938af92932ca6c043baeb
# BPI Challenge 2017 (doi:10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b)
fetch https://data.4tu.nl/file/34c3f44b-3101-4ea9-8281-e38905c68b8d/f3aec4f7-d52c-4217-82f4-57d719a8298c \
  bpic2017.xes.gz 10b37a2f78e870d78406198403ff13d2
# BPI Challenge 2019 (doi:10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1), ~730 MB
fetch https://data.4tu.nl/file/35ed7122-966a-484e-a0e1-749b64e3366d/864493d1-3a58-47f6-ad6f-27f95f995828 \
  bpic2019.xes 4eb909242351193a61e1c15b9c3cc814
