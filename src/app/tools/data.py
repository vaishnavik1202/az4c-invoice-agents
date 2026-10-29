"""Read-only lookups against data/ap.sqlite. Agents never see SQL or the whole database."""
import sqlite3
from functools import lru_cache

from app.config import db_path


@lru_cache(maxsize=1)
def _con() -> sqlite3.Connection:
    # Open read-only so the source data can never be changed (COMMON.md ground rule)
    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _rows(sql: str, *args) -> list[dict]:
    return [dict(r) for r in _con().execute(sql, args)]


def _one(sql: str, *args) -> dict | None:
    rows = _rows(sql, *args)
    return rows[0] if rows else None


def get_invoice(invoice_id: str) -> dict | None:
    """Invoice header plus its lines."""
    inv = _one("SELECT * FROM invoices WHERE invoice_id = ?", invoice_id)
    if inv:
        inv["lines"] = _rows(
            "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY line_no", invoice_id
        )
    return inv


def get_purchase_order(po_id: str) -> dict | None:
    """PO header plus its lines, or None if the PO does not exist."""
    po = _one("SELECT * FROM purchase_orders WHERE po_id = ?", po_id)
    if po:
        po["lines"] = _rows("SELECT * FROM po_lines WHERE po_id = ? ORDER BY line_no", po_id)
    return po


def get_goods_receipts(po_id: str) -> list[dict]:
    """All GRNs for a PO, each with its lines. Empty list = no goods receipt recorded."""
    grns = _rows("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_date", po_id)
    for g in grns:
        g["lines"] = _rows("SELECT * FROM grn_lines WHERE grn_id = ? ORDER BY po_line_no", g["grn_id"])
    return grns


def get_vendor(vendor_id: str) -> dict | None:
    return _one("SELECT * FROM vendors WHERE vendor_id = ?", vendor_id)


def get_item(item_code: str) -> dict | None:
    return _one("SELECT * FROM items WHERE item_code = ?", item_code)


def get_vendor_invoices(vendor_id: str) -> list[dict]:
    """Every invoice in our books from this vendor, with payment status if paid."""
    return _rows(
        """SELECT i.invoice_id, i.vendor_invoice_no, i.invoice_date, i.total,
                  p.payment_id, p.paid_on, p.status AS payment_status
           FROM invoices i LEFT JOIN payments p ON p.invoice_id = i.invoice_id
           WHERE i.vendor_id = ? ORDER BY i.invoice_date""",
        vendor_id,
    )
