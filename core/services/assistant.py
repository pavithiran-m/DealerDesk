import json
import os
import re
from decimal import Decimal
from typing import Dict, List, Tuple, Any, Optional
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from core.models import Product, Dealer, Order, OrderItem, ChatMessage
from core.serializers import OrderSerializer
from core.utils import calculate_line_item, generate_order_number

# Tool definitions schema for Groq / OpenAI-compatible tool calling
ASSISTANT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Search the catalogue for spare parts by query keyword, vehicle fitment, or category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Part name, SKU, or keyword to search for, e.g. 'brake pads', 'engine oil'.",
                    },
                    "vehicle": {
                        "type": "string",
                        "description": "Vehicle model name if mentioned, e.g. 'Bajaj Pulsar 150', 'Dominar 400'.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category name if known, e.g. 'Brakes', 'Engine & Oils'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check current live available inventory and catalogue price for a specific product SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "Exact product SKU code, e.g. 'BRK-1044'.",
                    },
                },
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_draft_order",
            "description": "Create a new DRAFT order for the dealer with the requested parts and quantities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of items to order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string", "description": "Product SKU"},
                                "quantity": {"type": "integer", "description": "Quantity to order (minimum 1)"},
                            },
                            "required": ["sku", "quantity"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are DealerDesk AI, an intelligent assistant for motorcycle and automotive parts, components, and accessories distribution.

CATALOGUE & PRODUCT SCOPE:
- Our warehouse stocks both motorcycle/vehicle spare parts AND vehicle accessories (including Mobile Holders, Handlebar Mounts, USB Chargers, Brakes, Chains, Filters, Engine Oils, Batteries, Electricals, etc.).
- ALWAYS invoke the `search_parts` tool to search our live database for ANY product or accessory enquiry (including mobile holders, phone mounts, accessories, etc.). NEVER say you only handle spare parts, and NEVER reject an inquiry!
- For Universal Accessories (like Mobile Holders): they have Universal fitment across all motorcycle brands (Yamaha, Bajaj, Royal Enfield, Honda, etc.) and are stocked under accessory brands (such as Moto Torque, Tukzer, Portronics).
- When presenting search results, ALWAYS output a clean Markdown table with:
  | SKU | Product Name | Brand | Price (₹) | Available Stock | Vehicle Fitment |
- For universal accessories (e.g. Mobile Holders), explain that fitment is Universal for all motorcycles/handlebars without requiring a vehicle-specific part. Then ask the user if they have a preferred brand (e.g. Moto Torque, Tukzer, Portronics) or if they would like to place an order.
- Always include the relevant SKU codes in your response when recommending products.
- Never guess or invent stock levels or prices. Always call the tools.
- You can ONLY create DRAFT orders.
- Automatically correct minor user typos (e.g. 'holerd' -> 'holder', 'brek' -> 'brake', 'yemaga' -> 'yamaha').
"""

TYPO_CORRECTIONS = {
    "holerd": "holder",
    "holer": "holder",
    "holdr": "holder",
    "munt": "mount",
    "mountr": "mount",
    "phne": "phone",
    "fone": "phone",
    "brak": "brake",
    "brek": "brake",
    "spoket": "sprocket",
    "sprocet": "sprocket",
    "chan": "chain",
    "flter": "filter",
    "filtr": "filter",
    "yemaga": "yamaha",
    "yamha": "yamaha",
    "yemaha": "yamaha",
    "plsr": "pulsar",
    "pulsr": "pulsar",
    "actva": "activa",
}


def execute_search_parts(query: str = "", vehicle: str = "", category: str = "") -> dict:
    qs = Product.objects.all()
    
    if query:
        clean_q = query.strip()
        # Apply typo correction dictionary
        for typo, fix in TYPO_CORRECTIONS.items():
            clean_q = re.sub(rf"\b{re.escape(typo)}\b", fix, clean_q, flags=re.IGNORECASE)

        # Direct exact match first
        exact_qs = qs.filter(
            Q(name__icontains=clean_q)
            | Q(sku__icontains=clean_q)
            | Q(description__icontains=clean_q)
            | Q(brand__icontains=clean_q)
        )
        if exact_qs.exists():
            qs = exact_qs
        else:
            # Tokenized matching with singularization (e.g. 'brake pads' -> 'brake' & 'pad')
            tokens = [w.strip() for w in clean_q.split() if len(w.strip()) > 2]
            q_and = Q()
            for t in tokens:
                s = t.rstrip("s")  # handle plural 'pads' -> 'pad'
                q_and &= (
                    Q(name__icontains=s)
                    | Q(sku__icontains=s)
                    | Q(category__icontains=s)
                    | Q(brand__icontains=s)
                    | Q(vehicle_fitment__icontains=s)
                )
            token_qs = qs.filter(q_and)
            if token_qs.exists():
                qs = token_qs
            else:
                # Fallback: tokenized OR matching
                q_or = Q()
                for t in tokens:
                    s = t.rstrip("s")
                    q_or |= Q(name__icontains=s) | Q(category__icontains=s)
                or_qs = qs.filter(q_or)
                if or_qs.exists():
                    qs = or_qs

    if vehicle:
        v_clean = vehicle.strip()
        v_exact = qs.filter(vehicle_fitment__icontains=v_clean)
        if v_exact.exists():
            qs = v_exact
        else:
            # Try sub-model (e.g. 'Pulsar' from 'Bajaj Pulsar 150')
            v_tokens = [w for w in v_clean.split() if len(w) > 3]
            for vt in v_tokens:
                vt_qs = qs.filter(vehicle_fitment__icontains=vt)
                if vt_qs.exists():
                    qs = vt_qs
                    break

    if category:
        c_clean = category.strip()
        c_qs = qs.filter(category__icontains=c_clean)
        if c_qs.exists():
            qs = c_qs

    matches = qs[:10]
    results = []
    for p in matches:
        results.append({
            "sku": p.sku,
            "name": p.name,
            "category": p.category,
            "brand": p.brand,
            "vehicle_fitment": p.vehicle_fitment,
            "price": f"{p.price:.2f}",
            "stock": p.stock,
        })
    return {"count": len(results), "products": results}


def execute_check_stock(sku: str) -> dict:
    try:
        p = Product.objects.get(sku=sku.strip())
        return {
            "sku": p.sku,
            "name": p.name,
            "price": f"{p.price:.2f}",
            "available_stock": p.stock,
            "vehicle_fitment": p.vehicle_fitment,
        }
    except Product.DoesNotExist:
        return {"error": f"Product with SKU '{sku}' not found"}


def execute_create_draft_order(dealer_id: int, items: list) -> Tuple[dict, Optional[str]]:
    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except Dealer.DoesNotExist:
        return {"error": f"Dealer with ID {dealer_id} not found"}, None

    if not items:
        return {"error": "At least one item required"}, None

    skus = [it.get("sku") for it in items]
    prods = {p.sku: p for p in Product.objects.filter(sku__in=skus)}

    with transaction.atomic():
        order_num = generate_order_number()
        order = Order.objects.create(
            order_number=order_num,
            dealer=dealer,
            status="DRAFT",
        )
        subtotal_acc = Decimal("0.00")
        tax_acc = Decimal("0.00")

        for it in items:
            sku = it.get("sku")
            qty = it.get("quantity", 1)
            if sku not in prods:
                return {"error": f"Unknown SKU {sku}"}, None
            prod = prods[sku]
            line_sub, line_tax, line_tot = calculate_line_item(prod.price, qty)

            OrderItem.objects.create(
                order=order,
                product=prod,
                name=prod.name,
                quantity=qty,
                unit_price=prod.price,
                line_subtotal=line_sub,
                line_tax=line_tax,
                line_total=line_tot,
            )
            subtotal_acc += line_sub
            tax_acc += line_tax

        order.subtotal = subtotal_acc
        order.tax_total = tax_acc
        order.grand_total = subtotal_acc + tax_acc
        order.save()

    return {
        "status": "DRAFT",
        "order_number": order.order_number,
        "grand_total": f"{order.grand_total:.2f}",
    }, order.order_number


def sanitize_db_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u20b9", "Rs. ")
    try:
        return s.encode("cp1252", errors="replace").decode("cp1252")
    except Exception:
        return s


def run_assistant_turn(session_id: str, dealer_id: int, user_message: str) -> dict:
    """Processes a single conversational turn with session memory, tool calling, and grounded response."""
    # Record user message in session memory
    ChatMessage.objects.create(
        session_id=session_id,
        dealer_id=dealer_id,
        role="user",
        content=sanitize_db_text(user_message),
    )

    # Fetch last 10 messages for this session
    history_records = ChatMessage.objects.filter(session_id=session_id).order_by("created_at")[:10]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history_records:
        messages.append({"role": m.role, "content": m.content})

    reported_tool_calls = []
    actions = []
    cited_skus = set()

    # Determine active LLM provider (Groq, OpenAI, Gemini)
    groq_api_key = os.environ.get("GROQ_API_KEY", "") or getattr(settings, "GROQ_API_KEY", "")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "") or getattr(settings, "OPENAI_API_KEY", "")
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "") or getattr(settings, "GEMINI_API_KEY", "")

    active_llm = None
    if groq_api_key and groq_api_key != "your-groq-api-key-here":
        active_llm = {
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "headers": {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
            "model": "openai/gpt-oss-120b",
        }
    elif openai_api_key and openai_api_key != "your-openai-api-key-here":
        active_llm = {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": {"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
            "model": "gpt-4o-mini",
        }
    elif gemini_api_key and gemini_api_key != "your-gemini-api-key-here":
        active_llm = {
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {"Authorization": f"Bearer {gemini_api_key}", "Content-Type": "application/json"},
            "model": "gemini-1.5-flash",
        }

    if active_llm:
        try:
            import requests as req
            payload = {
                "model": active_llm["model"],
                "messages": messages,
                "tools": ASSISTANT_TOOLS,
                "tool_choice": "auto",
                "max_tokens": 800,
            }
            resp = req.post(active_llm["url"], headers=active_llm["headers"], json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                response_message = choice["message"]
                tool_calls = response_message.get("tool_calls") or []

                if tool_calls:
                    messages.append(response_message)
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "")
                        args_raw = fn.get("arguments", {})
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        reported_tool_calls.append({"name": fn_name, "arguments": args})

                        if fn_name == "search_parts":
                            tool_result = execute_search_parts(
                                query=args.get("query", ""),
                                vehicle=args.get("vehicle", ""),
                                category=args.get("category", ""),
                            )
                        elif fn_name == "check_stock":
                            sku = args.get("sku", "")
                            tool_result = execute_check_stock(sku)
                        elif fn_name == "create_draft_order":
                            tool_result, order_no = execute_create_draft_order(
                                dealer_id=dealer_id,
                                items=args.get("items", []),
                            )
                            if order_no:
                                actions.append({
                                    "type": "draft_order_created",
                                    "order_number": order_no,
                                })
                        else:
                            tool_result = {"error": f"Unknown tool {fn_name}"}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", "tc_1"),
                            "content": json.dumps(tool_result),
                        })

                    sec_payload = {
                        "model": active_llm["model"],
                        "messages": messages,
                        "max_tokens": 800,
                    }
                    sec_resp = req.post(active_llm["url"], headers=active_llm["headers"], json=sec_payload, timeout=30)
                    if sec_resp.status_code == 200:
                        sec_data = sec_resp.json()
                        final_reply = sec_data["choices"][0]["message"].get("content", "")
                    else:
                        final_reply = response_message.get("content") or "Here is the information from our catalogue."
                else:
                    llm_content = response_message.get("content") or ""
                    lowered_usr = user_message.lower()
                    has_refusal = any(r in llm_content.lower() for r in ["only handle", "unable to process", "sorry, but i", "cannot process", "not able to process", "spare-part requests"])
                    is_product_query = any(k in lowered_usr for k in [
                        "holder", "holerd", "phone", "mobile", "mount", "accessory",
                        "brake", "break", "pad", "chain", "sprocket", "oil", "coolant",
                        "filter", "plug", "battery", "light", "tyre", "tire", "disc", "shoe"
                    ]) or any(b in lowered_usr for b in ["buy", "need", "want", "search", "show", "price", "cost", "available", "stock"])

                    if has_refusal or (is_product_query and not re.search(r"\b[A-Z]{3}-\d{4}\b", llm_content)):
                        clean_q = re.sub(
                            r"\b(i\s+want\s+to\s+(buy|order|get|see)|do\s+you\s+have|show\s+me|find|cost\s+of|price\s+of|what\s+is\s+the|search\s+for|can\s+i\s+get|give\s+me|any|some|in\s+stock|available)\b",
                            "",
                            lowered_usr,
                            flags=re.IGNORECASE
                        ).strip()
                        clean_q = re.sub(r"[^\w\s-]", " ", clean_q).strip()
                        search_q = clean_q if clean_q else "spare parts"

                        # Extract vehicle if mentioned
                        vehicle = ""
                        for v in ["pulsar", "fz-s", "r15", "hunter 350", "classic 350", "activa", "splendor", "duke", "xtreme"]:
                            if v in lowered_usr:
                                vehicle = v.title()
                                break

                        tool_res = execute_search_parts(query=search_q, vehicle=vehicle)
                        products = tool_res.get("products", [])
                        if products:
                            reported_tool_calls.append({"name": "search_parts", "arguments": {"query": search_q, "vehicle": vehicle}})
                            for p in products:
                                cited_skus.add(p["sku"])

                            is_universal = any("holder" in p["name"].lower() or p["vehicle_fitment"] == "Universal" for p in products)

                            lines = ["Here are the matching items available in our warehouse catalogue:"]
                            lines.append("")
                            lines.append("| SKU | Product Name | Brand | Price (₹) | Available Stock | Vehicle Fitment |")
                            lines.append("|:---|:---|:---|:---|:---|:---|")
                            for p in products[:6]:
                                lines.append(f"| `{p['sku']}` | {p['name']} | {p['brand']} | ₹{p['price']} | {p['stock']} units | {p['vehicle_fitment']} |")
                            lines.append("")
                            if is_universal:
                                lines.append("💡 **Universal Fitment**: Mobile holders feature universal handlebar mounting and fit all motorcycle makes (Yamaha, Bajaj, Royal Enfield, Honda, TVS, etc.) without requiring a vehicle-specific part.")
                                lines.append("")
                                lines.append("You can click **`🛒 Buy / +Cart`** on any item to add it to your order cart, or let me know if you prefer a specific brand (e.g. **" + ", ".join(list(set(p['brand'] for p in products[:4]))) + "**)!")
                            else:
                                lines.append("You can click **`🛒 Buy / +Cart`** to add to your order, or let me know if you prefer a specific brand or model fitment.")
                            final_reply = "\n".join(lines)
                        else:
                            final_reply = llm_content or "How can I help you with your spare parts or accessories inquiry?"
                    else:
                        final_reply = llm_content or "How can I help you with your spare parts or accessories inquiry?"

                ChatMessage.objects.create(
                    session_id=session_id,
                    dealer_id=dealer_id,
                    role="assistant",
                    content=sanitize_db_text(final_reply),
                )

                found_skus = set(re.findall(r"\b[A-Z]{3}-\d{4}\b", final_reply))
                valid_existing_skus = set(Product.objects.filter(sku__in=found_skus).values_list("sku", flat=True))
                cited_skus.update(valid_existing_skus)

                return {
                    "reply": final_reply,
                    "cited_skus": sorted(list(cited_skus)),
                    "tool_calls": reported_tool_calls,
                    "actions": actions,
                }
        except Exception:
            pass


    # Heuristic / Deterministic Fallback if external LLM provider is busy or offline
    lowered = user_message.lower()
    final_reply = ""

    # 1. Order Creation Request
    order_sku_match = re.search(r"\b([A-Z]{3}-\d{4})\b", user_message)
    qty_match = re.search(r"(\d+)\s*(units|pcs|pieces|sets|items)?", user_message)
    if any(k in lowered for k in ["order", "buy", "purchase"]) and order_sku_match and qty_match:
        sku = order_sku_match.group(1)
        qty = int(qty_match.group(1))
        reported_tool_calls.append({
            "name": "create_draft_order",
            "arguments": {"items": [{"sku": sku, "quantity": qty}]},
        })
        tool_result, order_no = execute_create_draft_order(dealer_id, [{"sku": sku, "quantity": qty}])
        if order_no:
            actions.append({"type": "draft_order_created", "order_number": order_no})
            cited_skus.add(sku)
            final_reply = f"I have created a DRAFT order **{order_no}** for {qty} units of SKU `{sku}`."
        else:
            final_reply = f"Could not create draft order: {tool_result.get('error', 'unknown error')}"

    # 2. Specific Stock Check by SKU
    elif order_sku_match and any(k in lowered for k in ["stock", "price", "cost", "available", "check"]):
        sku = order_sku_match.group(1)
        reported_tool_calls.append({
            "name": "check_stock",
            "arguments": {"sku": sku},
        })
        stock_res = execute_check_stock(sku)
        if "error" not in stock_res:
            cited_skus.add(sku)
            final_reply = (
                f"**{stock_res['name']}** (`{sku}`)\n"
                f"- Available Warehouse Stock: **{stock_res['available_stock']} units**\n"
                f"- Unit Price: **₹{stock_res['price']}**\n"
                f"- Fitment: {stock_res.get('vehicle_fitment', 'Universal')}"
            )
        else:
            final_reply = f"SKU `{sku}` was not found in our catalog."

    # 3. Dynamic Product Search for any query (e.g. mobile holder, brake pad, oil, etc.)
    else:
        # Extract meaningful search terms
        clean_q = re.sub(
            r"\b(i\s+want\s+to\s+(buy|order|get|see)|do\s+you\s+have|show\s+me|find|cost\s+of|price\s+of|what\s+is\s+the|search\s+for|can\s+i\s+get|give\s+me|any|some|in\s+stock|available)\b",
            "",
            lowered,
            flags=re.IGNORECASE
        ).strip()
        clean_q = re.sub(r"[^\w\s-]", " ", clean_q).strip()

        # Extract vehicle if mentioned
        vehicle = ""
        for v in ["pulsar", "fz-s", "r15", "hunter 350", "classic 350", "activa", "splendor", "duke", "xtreme"]:
            if v in lowered:
                vehicle = v.title()
                break

        search_query = clean_q if clean_q else "spare parts"
        reported_tool_calls.append({
            "name": "search_parts",
            "arguments": {"query": search_query, "vehicle": vehicle},
        })
        search_res = execute_search_parts(query=search_query, vehicle=vehicle)
        products = search_res.get("products", [])

        if products:
            for p in products[:5]:
                cited_skus.add(p["sku"])
            
            is_universal = any("holder" in p["name"].lower() or p["vehicle_fitment"] == "Universal" for p in products)

            lines = ["Here are the matching items available in our warehouse catalogue:"]
            lines.append("")
            lines.append("| SKU | Product Name | Brand | Price (₹) | Available Stock | Vehicle Fitment |")
            lines.append("|:---|:---|:---|:---|:---|:---|")
            for p in products[:6]:
                lines.append(f"| `{p['sku']}` | {p['name']} | {p['brand']} | ₹{p['price']} | {p['stock']} units | {p['vehicle_fitment']} |")
            lines.append("")
            if is_universal:
                lines.append("💡 **Universal Fitment**: Mobile holders feature universal handlebar mounting and fit all motorcycle makes (Yamaha, Bajaj, Royal Enfield, Honda, TVS, etc.) without requiring a vehicle-specific part.")
                lines.append("")
                lines.append("You can click **`🛒 Buy / +Cart`** on any item to add it to your order cart, or let me know if you prefer a specific brand (e.g. **" + ", ".join(list(set(p['brand'] for p in products[:4]))) + "**)!")
            else:
                lines.append("You can click **`🛒 Buy / +Cart`** to add to your order, or let me know if you prefer a specific brand or model fitment.")
            final_reply = "\n".join(lines)
        elif any(g in lowered for g in ["hi", "hello", "hey", "help", "who are you"]):
            final_reply = (
                "👋 Hello! I am **DealerDesk AI Copilot**, connected live to your PostgreSQL warehouse.\n\n"
                "Here is what I can do for your dealership:\n"
                "1. **Find Parts**: Ask *'Show me mobile holders'* or *'Brake pads for Yamaha FZ-S'*\n"
                "2. **Check Stock**: Ask *'Check stock and price for ACC-1003'*\n"
                "3. **Draft Orders**: Ask *'Create a draft order for 3 units of ACC-1003'*\n\n"
                "What spare parts do you need today?"
            )
        else:
            final_reply = (
                f"I searched our warehouse catalog for *'{search_query}'* but could not find exact matches.\n"
                "Could you specify a vehicle model (e.g. *Yamaha FZ-S*, *Pulsar 150*, *Hunter 350*) or category (e.g. *Brakes*, *Accessories*, *Filters*)?"
            )

    ChatMessage.objects.create(
        session_id=session_id,
        dealer_id=dealer_id,
        role="assistant",
        content=sanitize_db_text(final_reply),
    )

    return {
        "reply": final_reply,
        "cited_skus": sorted(list(cited_skus)),
        "tool_calls": reported_tool_calls,
        "actions": actions,
    }
