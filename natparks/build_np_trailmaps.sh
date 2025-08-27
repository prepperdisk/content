#!/usr/bin/env bash
# build_np_trailmaps_mac.sh — macOS-friendly (Bash 3.2 + curl + sed + grep + awk)
# Outputs national_parks_trailmaps.csv with: park,state,pdf_url

set -euo pipefail

OUT="national_parks_trailmaps.csv"
echo 'park,state,pdf_url' > "$OUT"

# Match trail-ish PDFs; include brochures as fallback
PDF_RE='(trail|hike|wilderness|backcountry|day[- ]?hike|campground|camp|rim|valley|loop|map|brochure)'

fetch_pdfs () {
  # $1 = page URL (e.g., https://www.nps.gov/acad/planyourvisit/maps.htm)
  local page="$1"
  local base_dir="${page%/*}/"

  # 1) extract hrefs that end in .pdf
  # 2) HTML-decode &amp; to & (common in hrefs)
  # 3) turn relative links into absolute using pure Bash
  curl -sL "$page" \
    | sed -nE 's/.*href="([^"#?]+\.pdf[^"]*)".*/\1/p' \
    | sed 's/&amp;/\&/g' \
    | while IFS= read -r href; do
        [ -z "$href" ] && continue
        case "$href" in
          http://*|https://*) abs="$href" ;;
          /*)                  abs="https://www.nps.gov$href" ;;
          *)                   abs="${base_dir}${href}" ;;
        esac
        printf '%s\n' "$abs"
      done \
    | awk '!seen[$0]++'     # de-dupe lines
}

append_rows () {
  local park="$1" state="$2" slug="$3"
  local base="https://www.nps.gov/${slug}/"
  local maps="${base}planyourvisit/maps.htm"

  # Gather candidates from /planyourvisit/maps.htm, then fallbacks
  pdfs="$(fetch_pdfs "$maps" || true)"
  if [[ -z "$pdfs" ]]; then
    for sub in planyourvisit/ learn/news/ learn/ ; do
      more="$(fetch_pdfs "${base}${sub}" || true)"
      [[ -n "$more" ]] && pdfs="${pdfs}"$'\n'"${more}"
    done
  fi

  # Prefer trail/wilderness/etc; if none, keep whatever PDFs (often brochure)
  picked="$(printf "%s\n" "$pdfs" | grep -E -i "$PDF_RE" || true)"
  if [[ -z "$picked" && -n "$pdfs" ]]; then
    picked="$pdfs"
  fi

  # Append to CSV; quote fields safely
  if [[ -n "$picked" ]]; then
    printf "%s\n" "$picked" \
      | awk '!seen[$0]++' \
      | while IFS= read -r url; do
          [[ -z "$url" ]] && continue
          p_esc="${park//\"/\"\"}"
          s_esc="${state//\"/\"\"}"
          u_esc="${url//\"/\"\"}"
          echo "\"$p_esc\",\"$s_esc\",\"$u_esc\"" >> "$OUT"
        done
  fi
}

# 63 National Parks: park|state|slug
PARKS='
Acadia National Park|ME|acad
American Samoa National Park|AS|npsa
Arches National Park|UT|arch
Badlands National Park|SD|badl
Big Bend National Park|TX|bibe
Biscayne National Park|FL|bisc
Black Canyon of the Gunnison National Park|CO|blca
Bryce Canyon National Park|UT|brca
Canyonlands National Park|UT|cany
Capitol Reef National Park|UT|care
Carlsbad Caverns National Park|NM|cave
Channel Islands National Park|CA|chis
Congaree National Park|SC|cong
Crater Lake National Park|OR|crla
Cuyahoga Valley National Park|OH|cuva
Death Valley National Park|CA;NV|deva
Denali National Park and Preserve|AK|dena
Dry Tortugas National Park|FL|drto
Everglades National Park|FL|ever
Gates of the Arctic National Park and Preserve|AK|gaar
Gateway Arch National Park|MO|jeff
Glacier National Park|MT|glac
Glacier Bay National Park and Preserve|AK|glba
Grand Canyon National Park|AZ|grca
Grand Teton National Park|WY|grte
Great Basin National Park|NV|grba
Great Sand Dunes National Park and Preserve|CO|grsa
Great Smoky Mountains National Park|TN;NC|grsm
Guadalupe Mountains National Park|TX|gumo
Haleakalā National Park|HI|hale
Hawaiʻi Volcanoes National Park|HI|havo
Hot Springs National Park|AR|hosp
Indiana Dunes National Park|IN|indu
Isle Royale National Park|MI|isro
Joshua Tree National Park|CA|jotr
Katmai National Park and Preserve|AK|katm
Kenai Fjords National Park|AK|kefj
Kings Canyon National Park|CA|seki
Kobuk Valley National Park|AK|kova
Lake Clark National Park and Preserve|AK|lacl
Lassen Volcanic National Park|CA|lavo
Mammoth Cave National Park|KY|maca
Mesa Verde National Park|CO|meve
Mount Rainier National Park|WA|mora
New River Gorge National Park and Preserve|WV|neri
North Cascades National Park|WA|noca
Olympic National Park|WA|olym
Petrified Forest National Park|AZ|pefo
Pinnacles National Park|CA|pinn
Redwood National and State Parks|CA|redw
Rocky Mountain National Park|CO|romo
Saguaro National Park|AZ|sagu
Sequoia National Park|CA|seki
Shenandoah National Park|VA|shen
Theodore Roosevelt National Park|ND|thro
Virgin Islands National Park|VI|viis
Voyageurs National Park|MN|voya
White Sands National Park|NM|whsa
Wind Cave National Park|SD|wica
Wrangell–St. Elias National Park and Preserve|AK|wrst
Yellowstone National Park|WY;MT;ID|yell
Yosemite National Park|CA|yose
Zion National Park|UT|zion
'

# Run all
while IFS='|' read -r park state slug; do
  [[ -z "$park" ]] && continue
  echo "Scraping: $park"
  append_rows "$park" "$state" "$slug"
  sleep 0.5
done <<< "$PARKS"

echo "Wrote $(($(wc -l < "$OUT") - 1)) rows to $OUT"

