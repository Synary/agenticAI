"""
Données SAP Business One simulées (mock).
Reproduit la structure des tables/API SAP B1 :
  - Business Partners (OCRD)
  - Items (OITM)
  - Sales Orders (ORDR) / Invoices (OINV)
  - Sales targets vs achievement (OUDO / UDO custom)
  - Margins by product / customer
  - Delays / gaps
En production, remplacer les fonctions *_loader par des appels
OData / Service Layer SAP B1 (ex: /b1s/v1/Invoices, /b1s/v1/BusinessPartners).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

# --- Seed pour reproductibilité ---
random.seed(42)

# --- Référentiels ---
CUSTOMERS = [
    {"CardCode": "C0001", "CardName": "Société Alpha SA",       "Segment": "Grand compte", "Region": "Casablanca", "SalesOwner": "Karim"},
    {"CardCode": "C0002", "CardName": "Beta Industries",        "Segment": "PME",          "Region": "Rabat",      "SalesOwner": "Sara"},
    {"CardCode": "C0003", "CardName": "Gamma Distribution",     "Segment": "PME",          "Region": "Casablanca", "SalesOwner": "Karim"},
    {"CardCode": "C0004", "CardName": "Delta Retail Group",     "Segment": "Grand compte", "Region": "Marrakech",  "SalesOwner": "Youssef"},
    {"CardCode": "C0005", "CardName": "Epsilon Logistics",      "Segment": "PME",          "Region": "Tanger",     "SalesOwner": "Sara"},
    {"CardCode": "C0006", "CardName": "Zeta Pharma",            "Segment": "Grand compte", "Region": "Casablanca", "SalesOwner": "Youssef"},
    {"CardCode": "C0007", "CardName": "Eta Construction",       "Segment": "PME",          "Region": "Agadir",     "SalesOwner": "Karim"},
    {"CardCode": "C0008", "CardName": "Theta Services",         "Segment": "TPE",          "Region": "Fès",        "SalesOwner": "Sara"},
]

PRODUCTS = [
    {"ItemCode": "P100", "ItemName": "Licence Pro",     "Category": "Logiciel",  "UnitCost": 800,  "UnitPrice": 2500},
    {"ItemCode": "P200", "ItemName": "Module Analytics","Category": "Logiciel",  "UnitCost": 600,  "UnitPrice": 1800},
    {"ItemCode": "P300", "ItemName": "Support Premium", "Category": "Service",   "UnitCost": 200,  "UnitPrice": 900},
    {"ItemCode": "P400", "ItemName": "Formation",       "Category": "Service",   "UnitCost": 150,  "UnitPrice": 700},
    {"ItemCode": "P500", "ItemName": "Hardware Server", "Category": "Matériel",  "UnitCost": 4200, "UnitPrice": 6500},
]

SALES_OWNERS = ["Karim", "Sara", "Youssef"]

# --- Générateur d'écritures commerciales (Invoices) ---
def _generate_invoices(n: int = 220) -> List[Dict[str, Any]]:
    invoices = []
    today = datetime.now()
    for i in range(n):
        cust = random.choice(CUSTOMERS)
        prod = random.choice(PRODUCTS)
        qty = random.randint(1, 8)
        # On étale sur 180 jours pour pouvoir faire des comparaisons mensuelles
        days_ago = random.randint(0, 180)
        date = today - timedelta(days=days_ago)
        amount = prod["UnitPrice"] * qty
        cost = prod["UnitCost"] * qty
        margin = amount - cost
        # Simule quelques retards de livraison / paiement
        delay_days = random.choice([0, 0, 0, 2, 5, 12, 30])
        invoices.append({
            "DocEntry": 10000 + i,
            "DocNum": 50000 + i,
            "CardCode": cust["CardCode"],
            "CardName": cust["CardName"],
            "Region": cust["Region"],
            "Segment": cust["Segment"],
            "SalesOwner": cust["SalesOwner"],
            "ItemCode": prod["ItemCode"],
            "ItemName": prod["ItemName"],
            "Category": prod["Category"],
            "Quantity": qty,
            "UnitPrice": prod["UnitPrice"],
            "UnitCost": prod["UnitCost"],
            "DocTotal": amount,
            "CostTotal": cost,
            "GrossMargin": margin,
            "MarginPct": round(margin / amount * 100, 2) if amount else 0,
            "DocDate": date.strftime("%Y-%m-%d"),
            "PaymentDelayDays": delay_days,
            "Status": "Closed" if days_ago > 30 else "Open",
        })
    return invoices


INVOICES = _generate_invoices(220)


# --- Objectifs (par mois / commercial / région) ---
def _generate_targets() -> List[Dict[str, Any]]:
    today = datetime.now()
    targets = []
    for month_offset in range(6):
        d = (today.replace(day=1) - timedelta(days=30 * month_offset))
        month = d.strftime("%Y-%m")
        for owner in SALES_OWNERS:
            targets.append({
                "Period": month,
                "SalesOwner": owner,
                "TargetRevenue": random.choice([180000, 220000, 250000, 300000]),
                "TargetMarginPct": random.choice([38, 40, 42, 45]),
            })
    return targets


TARGETS = _generate_targets()


# --- Chargeurs (à remplacer par appels SAP B1 Service Layer en prod) ---
def load_invoices() -> List[Dict[str, Any]]:
    return INVOICES


def load_customers() -> List[Dict[str, Any]]:
    return CUSTOMERS


def load_products() -> List[Dict[str, Any]]:
    return PRODUCTS


def load_targets() -> List[Dict[str, Any]]:
    return TARGETS


# --- Helper : filtre par période ---
def filter_by_period(rows: List[Dict[str, Any]], period: str | None) -> List[Dict[str, Any]]:
    """
    period accepté : 'YYYY-MM', 'YYYY-MM-DD:YYYY-MM-DD', None (tout), 'last_30d', 'current_month'
    """
    if not period:
        return rows

    today = datetime.now()

    if period == "current_month":
        prefix = today.strftime("%Y-%m")
        return [r for r in rows if r["DocDate"].startswith(prefix)]

    if period == "last_30d":
        cutoff = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        return [r for r in rows if r["DocDate"] >= cutoff]

    if ":" in period:
        start, end = period.split(":")
        return [r for r in rows if start <= r["DocDate"] <= end]

    # Sinon 'YYYY-MM'
    return [r for r in rows if r["DocDate"].startswith(period)]