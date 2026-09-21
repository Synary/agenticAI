"""
Outils SAP Business One exposés à l'agent LangGraph.
Chaque outil est un @tool LangChain qui interroge les données mock
(à remplacer par appels OData / Service Layer SAP B1).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from config import logger
from sap_mock_data import (
    load_invoices, load_targets, load_customers, load_products,
    filter_by_period,
)


# --- Helpers ---
def _aggregate(rows: List[Dict[str, Any]], key_field: str) -> Dict[str, Dict[str, float]]:
    agg: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"revenue": 0.0, "cost": 0.0, "margin": 0.0, "qty": 0, "count": 0}
    )
    for r in rows:
        k = r.get(key_field, "N/A")
        agg[k]["revenue"] += r["DocTotal"]
        agg[k]["cost"]    += r["CostTotal"]
        agg[k]["margin"]  += r["GrossMargin"]
        agg[k]["qty"]     += r["Quantity"]
        agg[k]["count"]   += 1
    for k in agg:
        rev = agg[k]["revenue"] or 1
        agg[k]["margin_pct"] = round(agg[k]["margin"] / rev * 100, 2)
    return dict(agg)


def _fmt_currency(v: float) -> str:
    return f"{v:,.0f} MAD".replace(",", " ")


# ============================================================
#  1. CHIFFRE D'AFFAIRES
# ============================================================
@tool
def get_sap_revenue(
    period: str = "current_month",
    group_by: str = "none",
) -> str:
    """
    Retourne le chiffre d'affaires SAP Business One sur une période.

    Args:
        period: 'current_month', 'last_30d', 'YYYY-MM', ou 'YYYY-MM-DD:YYYY-MM-DD'
        group_by: 'none', 'SalesOwner', 'Region', 'Segment', 'Category', 'CardName'

    Returns:
        Résumé lisible du CA.
    """
    logger.info(f"[SAP] get_sap_revenue period={period} group_by={group_by}")
    try:
        rows = filter_by_period(load_invoices(), period)
        if not rows:
            return f"Aucune facture SAP trouvée pour la période '{period}'."

        total_rev = sum(r["DocTotal"] for r in rows)
        total_margin = sum(r["GrossMargin"] for r in rows)
        margin_pct = round(total_margin / total_rev * 100, 2) if total_rev else 0

        lines = [
            f"💰 CHIFFRE D'AFFAIRES SAP B1 — période '{period}'",
            f"   Factures analysées : {len(rows)}",
            f"   CA total : {_fmt_currency(total_rev)}",
            f"   Marge brute : {_fmt_currency(total_margin)} ({margin_pct}%)",
        ]

        if group_by != "none":
            agg = _aggregate(rows, group_by)
            top = sorted(agg.items(), key=lambda x: x[1]["revenue"], reverse=True)
            lines.append(f"\n📊 Répartition par {group_by} :")
            for name, v in top:
                lines.append(
                    f"   • {name}: {_fmt_currency(v['revenue'])} "
                    f"({v['margin_pct']}% marge, {v['count']} factures)"
                )
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] get_sap_revenue error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  2. RÉALISATIONS COMMERCIALES (vs objectifs)
# ============================================================
@tool
def get_sap_sales_performance(
    period: str = "current_month",
    sales_owner: Optional[str] = None,
) -> str:
    """
    Compare les réalisations commerciales SAP aux objectifs.

    Args:
        period: 'current_month', 'last_30d', 'YYYY-MM'
        sales_owner: filtre optionnel (ex: 'Karim')

    Returns:
        Tableau réalisé vs objectif par commercial.
    """
    logger.info(f"[SAP] get_sap_sales_performance period={period} owner={sales_owner}")
    try:
        rows = filter_by_period(load_invoices(), period)

        # Normalise la période pour matcher les objectifs (YYYY-MM)
        if period == "current_month":
            tgt_period = datetime.now().strftime("%Y-%m")
        elif period == "last_30d":
            tgt_period = datetime.now().strftime("%Y-%m")
        elif ":" in period:
            tgt_period = period.split(":")[0][:7]
        else:
            tgt_period = period

        targets = [t for t in load_targets() if t["Period"] == tgt_period]
        if sales_owner:
            targets = [t for t in targets if t["SalesOwner"].lower() == sales_owner.lower()]
            rows = [r for r in rows if r["SalesOwner"].lower() == sales_owner.lower()]

        if not targets:
            return f"Aucun objectif SAP défini pour la période '{tgt_period}'."

        agg = _aggregate(rows, "SalesOwner")

        lines = [f"🎯 RÉALISATIONS vs OBJECTIFS — {tgt_period}"]
        for t in targets:
            owner = t["SalesOwner"]
            realized = agg.get(owner, {})
            rev = realized.get("revenue", 0)
            margin_pct = realized.get("margin_pct", 0)
            target_rev = t["TargetRevenue"]
            target_margin = t["TargetMarginPct"]

            attainment = round(rev / target_rev * 100, 1) if target_rev else 0
            status = "✅ Atteint" if attainment >= 100 else ("🟡 Proche" if attainment >= 85 else "🔴 En retard")

            lines.append(
                f"\n   👤 {owner} — {status}"
                f"\n      CA réalisé : {_fmt_currency(rev)} / {_fmt_currency(target_rev)} "
                f"→ {attainment}%"
                f"\n      Marge : {margin_pct}% (objectif {target_margin}%)"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] get_sap_sales_performance error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  3. MARGES
# ============================================================
@tool
def get_sap_margins(
    period: str = "current_month",
    group_by: str = "Category",
    min_margin_pct: float = 0.0,
) -> str:
    """
    Analyse des marges SAP B1.

    Args:
        period: période d'analyse
        group_by: 'Category', 'ItemName', 'Region', 'Segment', 'CardName', 'SalesOwner'
        min_margin_pct: ne retourne que les groupes avec marge >= ce seuil

    Returns:
        Analyse de marge triée décroissante.
    """
    logger.info(f"[SAP] get_sap_margins period={period} group={group_by}")
    try:
        rows = filter_by_period(load_invoices(), period)
        if not rows:
            return f"Aucune donnée de marge SAP pour '{period}'."

        agg = _aggregate(rows, group_by)
        filtered = {k: v for k, v in agg.items() if v["margin_pct"] >= min_margin_pct}
        sorted_agg = sorted(filtered.items(), key=lambda x: x[1]["margin_pct"], reverse=True)

        if not sorted_agg:
            return f"Aucun groupe avec marge ≥ {min_margin_pct}%."

        lines = [f"📈 ANALYSE DE MARGES SAP — {period} (par {group_by})"]
        for name, v in sorted_agg:
            flag = "🔥" if v["margin_pct"] >= 50 else ("✅" if v["margin_pct"] >= 35 else "⚠️")
            lines.append(
                f"   {flag} {name}: {v['margin_pct']}% "
                f"(CA {_fmt_currency(v['revenue'])}, marge {_fmt_currency(v['margin'])}, "
                f"{v['count']} lignes)"
            )

        worst = min(sorted_agg, key=lambda x: x[1]["margin_pct"])
        lines.append(f"\n⚠️ Marge la plus faible : {worst[0]} à {worst[1]['margin_pct']}%")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] get_sap_margins error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  4. RETARDS / ÉCARTS OPÉRATIONNELS
# ============================================================
@tool
def get_sap_delays_and_gaps(
    period: str = "last_30d",
    threshold_days: int = 5,
) -> str:
    """
    Détecte les retards de paiement/livraison et les écarts opérationnels SAP B1.

    Args:
        period: période à analyser
        threshold_days: seuil de retard (jours) au-delà duquel un document est 'en retard'

    Returns:
        Liste des clients / docs en retard + statistiques.
    """
    logger.info(f"[SAP] get_sap_delays period={period} threshold={threshold_days}")
    try:
        rows = filter_by_period(load_invoices(), period)
        late = [r for r in rows if r.get("PaymentDelayDays", 0) >= threshold_days]
        if not late:
            return f"✅ Aucun retard > {threshold_days} jours sur '{period}'."

        by_customer: Dict[str, List[Dict]] = defaultdict(list)
        for r in late:
            by_customer[r["CardName"]].append(r)

        total_late_amount = sum(r["DocTotal"] for r in late)
        avg_delay = sum(r["PaymentDelayDays"] for r in late) / len(late)

        lines = [
            f"⏱️ RETARDS SAP — {period} (seuil ≥ {threshold_days}j)",
            f"   {len(late)} documents en retard — montant total : {_fmt_currency(total_late_amount)}",
            f"   Retard moyen : {avg_delay:.1f} jours",
            "",
            "   Top clients concernés :",
        ]

        for name, items in sorted(by_customer.items(), key=lambda x: -sum(r["DocTotal"] for r in x[1]))[:10]:
            amt = sum(r["DocTotal"] for r in items)
            max_delay = max(r["PaymentDelayDays"] for r in items)
            lines.append(f"   • {name} : {len(items)} doc(s), {_fmt_currency(amt)}, retard max {max_delay}j")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] get_sap_delays error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  5. LIAISON DÉCISION → IMPACT RÉEL
# ============================================================
@tool
def link_decision_to_impact(
    decision_text: str,
    impact_period: str = "last_30d",
) -> str:
    """
    Relie une décision de réunion à son impact SAP réel (CA, marge, retards).

    Args:
        decision_text: description de la décision (ex: 'focus sur les grands comptes à Casablanca')
        impact_period: période sur laquelle mesurer l'impact

    Returns:
        Corrélations observées entre la décision et les indicateurs SAP.
    """
    logger.info(f"[SAP] link_decision_to_impact: {decision_text[:80]}...")
    try:
        rows = filter_by_period(load_invoices(), impact_period)
        if not rows:
            return "Pas de données SAP sur la période d'impact."

        text = decision_text.lower()
        # Détection simple de mots-clés pour filtrer les dimensions
        filters: Dict[str, str] = {}
        if "grand compte" in text:
            filters["Segment"] = "Grand compte"
        if "pme" in text:
            filters["Segment"] = "PME"
        if "casablanca" in text:
            filters["Region"] = "Casablanca"
        if "rabat" in text:
            filters["Region"] = "Rabat"
        if "marrakech" in text:
            filters["Region"] = "Marrakech"
        if "tanger" in text:
            filters["Region"] = "Tanger"
        if "logiciel" in text:
            filters["Category"] = "Logiciel"
        if "service" in text:
            filters["Category"] = "Service"
        if "matériel" in text or "materiel" in text:
            filters["Category"] = "Matériel"
        for owner in ["karim", "sara", "youssef"]:
            if owner in text:
                filters["SalesOwner"] = owner.capitalize()

        def _match(r: Dict, f: Dict[str, str]) -> bool:
            return all(r.get(k) == v for k, v in f.items())

        impacted = [r for r in rows if _match(r, filters)] if filters else rows

        if not impacted:
            return (
                f"🔎 Décision : « {decision_text} »\n"
                f"Aucune donnée SAP ne correspond directement aux filtres détectés {filters}.\n"
                f"Interprétation : la décision n'a pas encore eu d'impact mesurable ou "
                f"les critères sont trop spécifiques."
            )

        rev = sum(r["DocTotal"] for r in impacted)
        margin = sum(r["GrossMargin"] for r in impacted)
        margin_pct = round(margin / rev * 100, 2) if rev else 0
        late = [r for r in impacted if r["PaymentDelayDays"] >= 5]

        lines = [
            f"🔗 IMPACT SAP DE LA DÉCISION",
            f"   Décision : « {decision_text} »",
            f"   Filtres détectés : {filters or 'aucun (analyse globale)'}",
            f"   Période d'impact : {impact_period}",
            "",
            f"   📊 CA concerné : {_fmt_currency(rev)}",
            f"   📈 Marge : {_fmt_currency(margin)} ({margin_pct}%)",
            f"   📄 Documents : {len(impacted)}",
            f"   ⏱️ Retards ≥5j : {len(late)} document(s)",
            "",
            "   Interprétation :",
        ]

        if margin_pct >= 40:
            lines.append("   ✅ Impact positif — la décision semble porter ses fruits sur la marge.")
        elif margin_pct >= 30:
            lines.append("   🟡 Impact modéré — surveiller l'évolution sur les prochaines semaines.")
        else:
            lines.append("   🔴 Impact faible — la décision n'a pas encore produit d'effet mesurable.")

        if late:
            lines.append(f"   ⚠️ {len(late)} retard(s) détecté(s) — risque opérationnel à adresser.")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] link_decision_to_impact error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  6. ÉCARTS OBJECTIFS ↔ RÉSULTATS
# ============================================================
@tool
def analyze_objective_gap(
    period: str = "current_month",
    dimension: str = "SalesOwner",
) -> str:
    """
    Identifie les écarts entre objectifs et résultats SAP B1.

    Args:
        period: période d'analyse
        dimension: 'SalesOwner', 'Region', 'Segment', 'Category'

    Returns:
        Diagnostic des écarts avec recommandations.
    """
    logger.info(f"[SAP] analyze_objective_gap period={period} dim={dimension}")
    try:
        rows = filter_by_period(load_invoices(), period)

        if period == "current_month":
            tgt_period = datetime.now().strftime("%Y-%m")
        elif period == "last_30d":
            tgt_period = datetime.now().strftime("%Y-%m")
        elif ":" in period:
            tgt_period = period.split(":")[0][:7]
        else:
            tgt_period = period

        agg = _aggregate(rows, dimension)
        targets = [t for t in load_targets() if t["Period"] == tgt_period]

        lines = [f"📉 ANALYSE DES ÉCARTS — {tgt_period} (par {dimension})"]

        if dimension == "SalesOwner" and targets:
            for t in targets:
                owner = t["SalesOwner"]
                realized = agg.get(owner, {})
                rev = realized.get("revenue", 0)
                target_rev = t["TargetRevenue"]
                gap = rev - target_rev
                gap_pct = round(gap / target_rev * 100, 1) if target_rev else 0
                sign = "+" if gap >= 0 else ""
                emoji = "✅" if gap >= 0 else ("🟡" if gap_pct > -15 else "🔴")
                lines.append(
                    f"   {emoji} {owner} : {sign}{_fmt_currency(gap)} ({sign}{gap_pct}%) "
                    f"[réalisé {_fmt_currency(rev)} / cible {_fmt_currency(target_rev)}]"
                )
        else:
            # Écart vs moyenne (pas d'objectif par dimension spécifique)
            if not agg:
                return f"Aucune donnée SAP pour {dimension} sur '{period}'."
            avg = sum(v["revenue"] for v in agg.values()) / len(agg)
            for name, v in sorted(agg.items(), key=lambda x: -x[1]["revenue"]):
                gap = v["revenue"] - avg
                emoji = "✅" if gap >= 0 else "🔴"
                lines.append(
                    f"   {emoji} {name} : {_fmt_currency(v['revenue'])} "
                    f"({'+' if gap >= 0 else ''}{_fmt_currency(gap)} vs moyenne)"
                )

        # Recommandations
        lines.append("\n💡 Recommandations :")
        weak = [l for l in lines if "🔴" in l]
        if weak:
            lines.append("   • Prioriser un plan d'action commercial sur les entités en rouge.")
            lines.append("   • Revoir le pricing ou mix produit pour restaurer la marge.")
        else:
            lines.append("   • Trajectoire conforme — maintenir le rythme actuel.")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] analyze_objective_gap error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# ============================================================
#  7. VUE GLOBALE (pour l'agent)
# ============================================================
@tool
def get_sap_snapshot(period: str = "current_month") -> str:
    """
    Vue globale SAP B1 : CA, marge, top clients, retards, réalisations.
    Idéal pour une question large du type 'où en est le business ?'.
    """
    logger.info(f"[SAP] get_sap_snapshot period={period}")
    try:
        rows = filter_by_period(load_invoices(), period)
        if not rows:
            return f"Aucune donnée SAP pour '{period}'."

        rev = sum(r["DocTotal"] for r in rows)
        margin = sum(r["GrossMargin"] for r in rows)
        margin_pct = round(margin / rev * 100, 2) if rev else 0
        late = [r for r in rows if r["PaymentDelayDays"] >= 5]

        by_customer = _aggregate(rows, "CardName")
        top5 = sorted(by_customer.items(), key=lambda x: -x[1]["revenue"])[:5]

        lines = [
            f"📊 SAP B1 SNAPSHOT — {period}",
            f"   CA : {_fmt_currency(rev)} | Marge : {_fmt_currency(margin)} ({margin_pct}%)",
            f"   Factures : {len(rows)} | Clients : {len(by_customer)} | Retards ≥5j : {len(late)}",
            "",
            "   🏆 Top 5 clients :",
        ]
        for name, v in top5:
            lines.append(f"      • {name} — {_fmt_currency(v['revenue'])} ({v['margin_pct']}%)")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[SAP] get_sap_snapshot error: {e}", exc_info=True)
        return f"Erreur SAP: {e}"


# --- Liste exportée pour main.py ---
sap_tools_list = [
    get_sap_revenue,
    get_sap_sales_performance,
    get_sap_margins,
    get_sap_delays_and_gaps,
    link_decision_to_impact,
    analyze_objective_gap,
    get_sap_snapshot,
]