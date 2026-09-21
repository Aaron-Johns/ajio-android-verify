import os
import re
import json
from pathlib import Path
from getpass import getpass
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai


# ============================================================
# CONFIG
# ============================================================

MODEL = "gemma-4-31b-it"

# Number of simultaneous Gemini requests.
MAX_WORKERS = 4

# Hard ceiling — MAX_WORKERS is clamped to this no matter what it's set to.
MAX_WORKERS_HARD_CAP = 4

# Folder containing this program.
ROOT_FOLDER = Path(__file__).resolve().parent


# ============================================================
# API KEY
# ============================================================

def get_api_key():

    api_key = os.environ.get("GEMINI_API_KEY")

    if api_key:
        print("[+] GEMINI_API_KEY found in environment.")
        return api_key.strip()

    print()
    print("[!] GEMINI_API_KEY was not found.")
    print("[+] Enter your Gemini API key.")
    print("[+] The key will not be saved.")
    print()

    api_key = getpass("Gemini API key: ").strip()

    if not api_key:
        raise RuntimeError("No Gemini API key supplied.")

    return api_key


API_KEY = get_api_key()

client = genai.Client(api_key=API_KEY)


# ============================================================
# GEMINI INSTRUCTIONS
# ============================================================

PROMPT = """
Analyze this ecommerce fashion banner carefully.

Return ONLY valid JSON.

Identify the following:

1. CLOTHING SEEN

List every clearly visible clothing or fashion item.

Examples:
- t-shirt
- shirt
- jeans
- trousers
- jacket
- dress
- hoodie
- sneakers
- shoes
- bag

Do not invent items that are not clearly visible.

2. BRANDS MENTIONED

Identify every clothing/fashion brand represented in the banner.

A brand counts if:

- its name is visibly written
- its wordmark is visible
- its recognizable graphical logo is visible

Graphical logos count even when they contain no readable text.

Do NOT classify generic promotional text as brands.

Examples of generic text:
- SALE
- SHOP NOW
- NEW
- OFFER
- COLLECTION
- OFF
- BUY NOW

3. DEAL OFFERED

Extract the promotional offer shown in the banner.

Preserve important numbers and meaning.

Examples:
- 40% OFF
- UP TO 50% OFF
- BUY 2 GET 1 FREE
- MIN 40% OFF

Do not invent a deal if none is clearly visible.

4. SPELLING ERRORS

Check all visible text in the banner (brand names excluded) for spelling errors.

List every misspelled word you find, along with the corrected spelling.

Examples of what counts:
- "SUMER SALE" -> "sumer" is misspelled ("summer")
- "COLLCTION" -> "collction" is misspelled ("collection")

Do not flag:
- Brand names or stylized brand wordmarks
- Intentional stylization (e.g. "SHOPPN'" as a deliberate style choice)
- Non-English words

If no spelling errors are visible, return an empty list.

5. VERIFICATION

If you cannot confidently determine the brand(s), OR you cannot confidently determine
the deal offered, verification is required.

In that situation:

"verification_required" must be true

and:

"verification" must contain exactly:

"User verification required"

Do not guess information simply to avoid verification.

Return EXACTLY this structure:

{
  "clothing_seen": [],
  "brands_mentioned": [],
  "deal_offered": "",
  "spelling_errors": [
    {"word": "", "correction": ""}
  ],
  "verification_required": false,
  "verification": ""
}

Do not add explanations outside the JSON.

Do not invent information.
"""


# ============================================================
# FIND IMAGE
# ============================================================

def find_banner_image(banner_folder):

    extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp"
    }

    images = [
        p for p in banner_folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in extensions
    ]

    if not images:
        return None

    # Prefer the normal downloaded image.
    preferred_names = {
        "image",
        "banner"
    }

    preferred = [
        p for p in images
        if p.stem.lower() in preferred_names
    ]

    if preferred:
        return preferred[0]

    return images[0]


# ============================================================
# ANALYZE ONE BANNER
# ============================================================

def analyze_banner(banner_folder):

    banner_folder = Path(banner_folder)

    image_path = find_banner_image(banner_folder)

    if image_path is None:
        return {
            "folder": banner_folder.name,
            "status": "error",
            "error": "No image found."
        }

    print(f"[START] {banner_folder.name}")

    try:

        # ----------------------------------------------------
        # Upload
        # ----------------------------------------------------

        uploaded_file = client.files.upload(
            file=str(image_path)
        )

        # ----------------------------------------------------
        # Gemini request
        # ----------------------------------------------------

        interaction = client.interactions.create(
            model=MODEL,
            input=[
                {
                    "type": "text",
                    "text": PROMPT
                },
                {
                    "type": "image",
                    "uri": uploaded_file.uri,
                    "mime_type": uploaded_file.mime_type
                }
            ]
        )

        raw = interaction.output_text.strip()

        # ----------------------------------------------------
        # Parse JSON
        # ----------------------------------------------------

        try:
            result = json.loads(raw)

        except json.JSONDecodeError:

            # Handle accidental markdown fences.
            cleaned = raw

            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1]

            if "```" in cleaned:
                cleaned = cleaned.split("```", 1)[0]

            cleaned = cleaned.strip()

            result = json.loads(cleaned)

        # ----------------------------------------------------
        # Defensive verification
        # ----------------------------------------------------

        brands = result.get("brands_mentioned", [])
        deal = result.get("deal_offered", "")

        if not brands or not deal:

            result["verification_required"] = True
            result["verification"] = "User verification required"

        else:

            result["verification_required"] = False
            result["verification"] = ""

        # ----------------------------------------------------
        # Metadata
        # ----------------------------------------------------

        result["_metadata"] = {
            "source_image": image_path.name,
            "model": MODEL
        }

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        output_path = banner_folder / "gemini_analysis.json"

        output_path.write_text(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        print(f"[DONE ] {banner_folder.name}")

        return {
            "folder": banner_folder.name,
            "status": "success",
            "path": str(output_path)
        }

    except Exception as e:

        print(f"[ERROR] {banner_folder.name}: {e}")

        return {
            "folder": banner_folder.name,
            "status": "error",
            "error": str(e)
        }


# ============================================================
# BANNER SELECTION
# ============================================================

def banner_number(folder):
    """Extract the trailing number from a banner folder name.

    "banner_015" -> 15, "banner_7" -> 7. Returns None if no number
    could be found.
    """
    match = re.search(r"(\d+)\s*$", folder.name)
    return int(match.group(1)) if match else None


def parse_banner_selection(text):
    """Parse input like "1,3,5-8" into a set of ints.

    Whitespace around commas/dashes is ignored. Raises ValueError on
    malformed input.
    """
    numbers = set()

    for part in text.split(","):
        part = part.strip()

        if not part:
            continue

        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start, end = int(start_str.strip()), int(end_str.strip())

            if end < start:
                start, end = end, start

            numbers.update(range(start, end + 1))

        else:
            numbers.add(int(part))

    return numbers


def get_banner_selection():
    """Ask which banner numbers to process. Blank input means all."""

    print()
    print("Enter banner numbers to process, e.g. 15  or  1,3,5-8")
    print("Leave blank to process every banner folder found.")
    text = input("Banners: ").strip()

    if not text:
        return None

    try:
        return parse_banner_selection(text)
    except ValueError:
        raise RuntimeError(f"Could not parse banner selection: {text!r}")


# ============================================================
# FIND BANNER FOLDERS
# ============================================================

def find_banner_folders(selected_numbers=None):
    """Find banner_* folders under ROOT_FOLDER.

    If `selected_numbers` is given, only folders whose trailing number
    is in that set are returned.
    """

    all_banners = sorted(
        p for p in ROOT_FOLDER.rglob("*")
        if (
            p.is_dir()
            and p.name.lower().startswith("banner_")
        )
    )

    if selected_numbers is None:
        return all_banners

    matched = [
        folder
        for folder in all_banners
        if banner_number(folder) in selected_numbers
    ]

    found_numbers = {banner_number(folder) for folder in matched}
    missing = sorted(selected_numbers - found_numbers)

    if missing:
        print(f"[!] No banner folder found for number(s): {missing}")

    return matched


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("AJIO GEMINI BANNER ANALYZER")
    print("=" * 70)

    concurrency = min(MAX_WORKERS, MAX_WORKERS_HARD_CAP)

    print(f"Root folder : {ROOT_FOLDER}")
    print(f"Model       : {MODEL}")
    print(f"Concurrency : {concurrency}")

    selected_numbers = get_banner_selection()
    banners = find_banner_folders(selected_numbers)

    if not banners:

        print()
        print("[ERROR] No matching banner_* folders found.")
        print(f"Searched under: {ROOT_FOLDER}")
        return

    print()
    print(f"[+] Found {len(banners)} banner folders.")
    print("[+] Starting parallel processing...")
    print()

    results = []

    # ========================================================
    # PARALLEL REQUESTS
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=concurrency
    ) as executor:

        future_map = {
            executor.submit(
                analyze_banner,
                banner
            ): banner
            for banner in banners
        }

        for future in as_completed(future_map):

            try:
                result = future.result()
                results.append(result)

            except Exception as e:

                banner = future_map[future]

                results.append({
                    "folder": banner.name,
                    "status": "error",
                    "error": str(e)
                })

    # ========================================================
    # SUMMARY
    # ========================================================

    successful = sum(
        1
        for r in results
        if r["status"] == "success"
    )

    failed = len(results) - successful

    print()
    print("=" * 70)
    print("FINISHED")
    print("=" * 70)

    print(f"Total     : {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed    : {failed}")

    if failed:

        print()
        print("Failed banners:")

        for result in results:

            if result["status"] == "error":
                print(
                    f"  {result['folder']}: "
                    f"{result.get('error', 'Unknown error')}"
                )


if __name__ == "__main__":
    main()