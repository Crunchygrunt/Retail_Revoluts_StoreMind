"""
Generates stress_test_catalog.csv — a wider, messier catalog than
messy_catalog.csv, built to find bugs before Cloud Run debugging has to.

Deliberately includes edge cases NOT covered by the original 10-row demo
file: scientific notation prices, price ranges, European-style thousand
separators, HTML embedded in names, genuine non-Latin product names
(distinct from mojibake — tests that Layer-0 doesn't false-positive on
real Unicode), triplicate SKUs, oversized descriptions, and a batch of
deliberately generic/thin names to stress-test the readiness score's
source_richness component at volume.

Run once to (re)generate:
    python3 generate_stress_catalog.py
"""

import csv
from pathlib import Path

rows = []


def add(sku, name, price, description="", image=""):
    rows.append({"sku": sku, "name": name, "price": price, "description": description, "image": image})


# ---- clean, well-formed rows across varied categories (25) --------------
add("ST-001", "Stainless Steel Water Bottle", "599", "Double-walled insulated bottle, keeps drinks cold for 24 hours.", "bottle.jpg")
add("ST-002", "Organic Cotton Bedsheet Set", "1899", "King-size 100% organic cotton bedsheet with two pillow covers.", "bedsheet.jpg")
add("ST-003", "Wireless Earbuds Pro", "3499", "Bluetooth 5.3 earbuds with active noise cancellation and 30hr battery.", "earbuds.jpg")
add("ST-004", "Hand-Poured Soy Candle", "349", "Lavender-scented soy wax candle, 40 hour burn time.", "candle.jpg")
add("ST-005", "Leather Messenger Bag", "2799", "Full-grain leather bag with laptop compartment, fits up to 15 inches.", "bag.jpg")
add("ST-006", "Ceramic Plant Pot Set", "799", "Set of 3 minimalist ceramic pots with drainage holes, various sizes.", "pots.jpg")
add("ST-007", "Merino Wool Beanie", "699", "Soft merino wool beanie, one size fits most, machine washable.", "beanie.jpg")
add("ST-008", "Cast Iron Skillet 12-inch", "1299", "Pre-seasoned cast iron skillet, oven-safe up to 500F.", "skillet.jpg")
add("ST-009", "Natural Rubber Yoga Mat", "1499", "6mm thick eco-friendly yoga mat with alignment lines.", "mat.jpg")
add("ST-010", "Bamboo Charging Stand", "899", "Sustainable bamboo stand for phone and watch charging.", "stand.jpg")
add("ST-011", "Wool Blend Throw Blanket", "1599", "Warm herringbone throw blanket, 130x170cm, dry clean only.", "blanket.jpg")
add("ST-012", "Stainless Steel French Press", "999", "34oz French press with double-mesh filter, borosilicate glass.", "press.jpg")
add("ST-013", "Cotton Canvas Sneakers", "1199", "Classic low-top canvas sneakers, unisex sizing, rubber sole.", "sneakers.jpg")
add("ST-014", "Handmade Soap Bar Set", "449", "Set of 4 cold-process soap bars, essential oil scented.", "soap.jpg")
add("ST-015", "Aluminum Laptop Stand", "1099", "Adjustable ergonomic laptop stand, folds flat for travel.", "laptopstand.jpg")
add("ST-016", "Woven Jute Rug", "2199", "Natural jute area rug, 5x7ft, reversible weave pattern.", "rug.jpg")
add("ST-017", "Glass Pour-Over Coffee Dripper", "649", "Borosilicate glass dripper with wooden collar, fits standard filters.", "dripper.jpg")
add("ST-018", "Recycled Polyester Backpack", "1899", "20L daypack made from recycled plastic bottles, water-resistant.", "backpack.jpg")
add("ST-019", "Beeswax Food Wraps", "399", "Set of 3 reusable beeswax wraps, replaces plastic cling film.", "wraps.jpg")
add("ST-020", "Ceramic Pour-Over Mug", "299", "12oz hand-glazed ceramic mug, microwave and dishwasher safe.", "mug2.jpg")
add("ST-021", "Linen Napkin Set", "549", "Set of 6 pure linen napkins, stone-washed for softness.", "napkins.jpg")
add("ST-022", "Cork Yoga Block", "399", "Natural cork yoga block, non-slip texture, lightweight.", "block.jpg")
add("ST-023", "Stainless Steel Lunch Box", "799", "Three-compartment stainless steel lunch box with lid clips.", "lunchbox.jpg")
add("ST-024", "Wooden Desk Organizer", "899", "Oak desk organizer with pen slots and phone stand.", "organizer.jpg")
add("ST-025", "Merino Wool Socks (3-Pack)", "649", "Cushioned merino wool blend socks, moisture wicking.", "socks.jpg")

# ---- missing / broken required fields (Layer-0 should reject) (8) -------
add("ST-026", "Copper Water Jug", "", "1L pure copper jug, ayurvedic health benefits.", "jug.jpg")  # missing price
add("ST-027", "", "899", "Description present but name is missing entirely.", "noname.jpg")  # missing name
add("ST-028", "   ", "499", "Name is whitespace-only, should be treated as empty.", "space.jpg")  # whitespace name
add("ST-029", "Bluetooth Speaker", "call for price", "Portable speaker, price on request.", "speaker.jpg")  # unparseable price
add("ST-030", "Desk Lamp", "-450", "Price entered as negative by mistake.", "lamp.jpg")  # negative price
add("ST-031", "Travel Pillow", "0", "Price entered as zero, likely a data entry error.", "pillow.jpg")  # zero price
add("ST-032", "Umbrella", "TBD", "Pricing not yet finalized.", "umbrella.jpg")  # unparseable price
add("ST-033", "Phone Case", "ask seller", "Contact seller for current price.", "case.jpg")  # unparseable price

# ---- price format edge cases (7) -----------------------------------------
add("ST-034", "Bath Towel Set", "Rs. 1,299", "Set of 2 cotton bath towels, 500 GSM.", "towel.jpg")  # currency prefix + thousands comma
add("ST-035", "Table Lamp", "INR 2499", "Ceramic base table lamp with linen shade.", "tablelamp.jpg")  # currency code prefix
add("ST-036", "Wall Clock", "€45.00", "Minimalist wall clock, silent sweep movement.", "clock.jpg")  # euro symbol
add("ST-037", "Picture Frame Set", "99.999", "Set of 5 wooden picture frames, various sizes.", "frames.jpg")  # 3 decimal digits, regex only allows 1-2
add("ST-038", "Garden Hose 15m", "4.99E2", "Expandable garden hose, brass fittings.", "hose.jpg")  # scientific notation
add("ST-039", "Office Chair", "199-299", "Ergonomic office chair, price varies by color.", "chair.jpg")  # price range, ambiguous
add("ST-040", "Area Rug 8x10", "1 299", "Machine-woven area rug, European-style price formatting.", "rug2.jpg")  # space thousands separator

# ---- name edge cases (8) --------------------------------------------------
add("ST-041", "X" * 250, "599", "Name deliberately exceeds the 200 character limit to test rejection.", "long.jpg")  # too long
add("ST-042", "<b>Premium</b> Leather Wallet", "1499", "Bifold leather wallet with RFID blocking.", "wallet.jpg")  # embedded HTML, should be accepted
add("ST-043", "हस्तनिर्मित ऊनी शॉल", "1899", "Hand-woven wool shawl from local artisans.", "shawl.jpg")  # genuine Hindi, should be accepted
add("ST-044", "手工编织羊毛毯", "2299", "Hand-knitted wool blanket, traditional pattern.", "blanket2.jpg")  # genuine Chinese, should be accepted
add("ST-045", "🎉 Party Balloon Set (50pc)", "349", "Assorted color latex balloons for celebrations.", "balloons.jpg")  # emoji in name, should be accepted
add("ST-046", "Caf\ufffd Mug Set", "599", "Corrupted encoding in the name — should trigger mojibake rejection.", "cafemug.jpg")  # replacement char, should be rejected
add("ST-047", "Sw\ufffdater", "1299", "Another mojibake case, single corrupted character mid-word.", "sweater.jpg")  # replacement char, should be rejected
add("ST-048", "  Padded  Envelope  Set  ", "249", "Set of 10 padded mailing envelopes, various sizes.", "envelopes.jpg")  # irregular whitespace, should be accepted+cleaned

# ---- deliberately generic/thin names (10) — stress-tests source_richness -
add("ST-049", "Item", "199", "", "")
add("ST-050", "Product", "299", "", "")
add("ST-051", "Thing", "99", "", "")
add("ST-052", "Stuff", "149", "", "")
add("ST-053", "Misc", "399", "", "")
add("ST-054", "Goods", "249", "", "")
add("ST-055", "Unit", "179", "", "")
add("ST-056", "X", "129", "", "")  # single character name
add("ST-057", "New Item 1", "89", "", "")
add("ST-058", "Untitled Product", "459", "", "")

# ---- duplicates (deliberate — 3 pairs + 1 triplicate) ---------------------
add("ST-059", "Insulated Tumbler", "599", "20oz stainless steel tumbler with lid.", "tumbler.jpg")
add("ST-059", "Insulated Tumbler (restock)", "649", "Duplicate SKU on purpose — should reject the second occurrence.", "tumbler2.jpg")
add("ST-060", "Denim Tote Bag", "999", "Sturdy denim tote with inner pocket.", "denimtote.jpg")
add("ST-060", "Denim Tote Bag v2", "1049", "Duplicate SKU, second occurrence.", "denimtote2.jpg")
add("ST-061", "Recycled Glass Tumbler Set", "849", "Set of 4 tumblers made from recycled glass.", "glasstumbler.jpg")
add("ST-061", "Recycled Glass Tumbler Set (dup)", "899", "Duplicate SKU, second occurrence.", "glasstumbler2.jpg")
add("ST-062", "Cork Coasters (Set of 6)", "299", "Natural cork coasters, heat resistant.", "coasters2.jpg")
add("ST-062", "Cork Coasters (dup 1)", "319", "Triplicate test — first duplicate.", "coasters3.jpg")
add("ST-062", "Cork Coasters (dup 2)", "329", "Triplicate test — second duplicate.", "coasters4.jpg")

# ---- oversized description (1) --------------------------------------------
add("ST-063", "Oversized Description Test Product", "199", "This description is deliberately extremely long. " * 200, "test.jpg")

# ---- SKU edge cases (2) -----------------------------------------------------
add("", "No SKU Provided Product", "349", "Row has no SKU at all — should fall back to a generated row id.", "nosku.jpg")
add("ST 064!", "Special Character SKU", "429", "SKU itself has a space and punctuation.", "specialsku.jpg")

# ---- CSV quoting stress (2) -------------------------------------------------
add("ST-065", "Gift Card, $50 Value", "5000", "Redeemable in-store or online, no expiration date.", "giftcard.jpg")  # comma in name
add("ST-066", 'Engraved "Best Friend" Keychain', "199", 'Stainless steel keychain engraved with "Best Friend".', "keychain.jpg")  # embedded quotes

# ---- completely empty row (1) -----------------------------------------------
add("", "", "", "", "")

out_path = Path(__file__).parent / "stress_test_catalog.csv"
with open(out_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["sku", "name", "price", "description", "image"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to {out_path}")
