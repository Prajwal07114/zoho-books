"""High-performance, pure-Python PDF document generation for Invoices."""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
import io


class InvoicePDFGenerator:
    """Generates standard, valid PDF 1.4 binary documents for Ledgerly sales invoices.
    
    Constructed directly in standard PDF syntax without requiring heavy native external C libraries.
    """

    @staticmethod
    def generate_invoice_pdf(
        invoice_number: str,
        invoice_date: date,
        due_date: date,
        customer_name: str,
        customer_email: Optional[str],
        org_name: str,
        currency_code: str,
        lines: List[Dict[str, Any]],
        subtotal: Decimal,
        tax_total: Decimal,
        total_amount: Decimal,
        settled_amount: Decimal,
        balance_due: Decimal,
        notes: Optional[str] = None,
    ) -> bytes:
        """Render an invoice into a clean, professional PDF 1.4 binary byte stream."""
        lines_text = []
        lines_text.append(f"BT /F1 16 Tf 50 780 Td ({org_name}) Tj ET")
        lines_text.append("BT /F1 10 Tf 50 765 Td (Commercial Sales Invoice) Tj ET")
        lines_text.append(f"BT /F1 14 Tf 400 780 Td (INVOICE: {invoice_number}) Tj ET")
        lines_text.append(f"BT /F2 9 Tf 400 765 Td (Date: {invoice_date}  |  Due: {due_date}) Tj ET")

        # Horizontal separator rule
        lines_text.append("0.8 w 50 750 m 550 750 l S")

        # Customer Details
        lines_text.append("BT /F1 10 Tf 50 730 Td (BILLED TO:) Tj ET")
        lines_text.append(f"BT /F2 10 Tf 50 715 Td ({customer_name}) Tj ET")
        if customer_email:
            lines_text.append(f"BT /F2 9 Tf 50 700 Td ({customer_email}) Tj ET")

        # Table Header
        lines_text.append("0.8 w 50 670 m 550 670 l S")
        lines_text.append("BT /F1 9 Tf 55 675 Td (Item & Description) Tj ET")
        lines_text.append("BT /F1 9 Tf 320 675 Td (Qty) Tj ET")
        lines_text.append("BT /F1 9 Tf 380 675 Td (Price) Tj ET")
        lines_text.append("BT /F1 9 Tf 480 675 Td (Total) Tj ET")
        lines_text.append("0.8 w 50 668 m 550 668 l S")

        y = 650
        for idx, line in enumerate(lines[:12], 1):  # Cap to fit single page elegantly
            desc = line.get("description", "Item")[:35].replace("(", "[").replace(")", "]")
            qty = f"{Decimal(str(line.get('quantity', 1))):,.2f}"
            price = f"${Decimal(str(line.get('unit_price', 0))):,.2f}"
            line_tot = f"${Decimal(str(line.get('subtotal', 0))):,.2f}"

            lines_text.append(f"BT /F2 9 Tf 55 {y} Td ({desc}) Tj ET")
            lines_text.append(f"BT /F2 9 Tf 320 {y} Td ({qty}) Tj ET")
            lines_text.append(f"BT /F2 9 Tf 380 {y} Td ({price}) Tj ET")
            lines_text.append(f"BT /F2 9 Tf 480 {y} Td ({line_tot}) Tj ET")
            y -= 20

        # Totals Section
        y_tot = max(y - 10, 350)
        lines_text.append(f"0.8 w 300 {y_tot} m 550 {y_tot} l S")
        lines_text.append(f"BT /F2 9 Tf 320 {y_tot - 15} Td (Subtotal:) Tj ET")
        lines_text.append(f"BT /F2 9 Tf 480 {y_tot - 15} Td (${subtotal:,.2f} {currency_code}) Tj ET")

        lines_text.append(f"BT /F2 9 Tf 320 {y_tot - 30} Td (Tax:) Tj ET")
        lines_text.append(f"BT /F2 9 Tf 480 {y_tot - 30} Td (${tax_total:,.2f} {currency_code}) Tj ET")

        lines_text.append(f"0.8 w 300 {y_tot - 35} m 550 {y_tot - 35} l S")
        lines_text.append(f"BT /F1 10 Tf 320 {y_tot - 50} Td (Grand Total:) Tj ET")
        lines_text.append(f"BT /F1 10 Tf 480 {y_tot - 50} Td (${total_amount:,.2f} {currency_code}) Tj ET")

        lines_text.append(f"BT /F2 9 Tf 320 {y_tot - 65} Td (Settled Amount:) Tj ET")
        lines_text.append(f"BT /F2 9 Tf 480 {y_tot - 65} Td (${settled_amount:,.2f} {currency_code}) Tj ET")

        lines_text.append(f"BT /F1 11 Tf 320 {y_tot - 85} Td (Balance Due:) Tj ET")
        lines_text.append(f"BT /F1 11 Tf 480 {y_tot - 85} Td (${balance_due:,.2f} {currency_code}) Tj ET")

        if notes:
            safe_notes = notes[:80].replace("(", "[").replace(")", "]")
            lines_text.append(f"BT /F2 8 Tf 50 150 Td (Notes: {safe_notes}) Tj ET")

        # Footer
        lines_text.append("BT /F2 8 Tf 50 80 Td (Generated securely by Ledgerly SaaS Accounting Spine. Legal Double-Entry Certified.) Tj ET")

        content_stream = "\n".join(lines_text).encode("latin-1")
        content_len = len(content_stream)

        # Assemble PDF 1.4 Object Graph
        objects = []
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>"
        )
        objects.append(
            f"<< /Length {content_len} >>\nstream\n".encode("ascii")
            + content_stream
            + b"\nendstream"
        )
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

        # Build output with cross-reference table
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []

        for i, obj in enumerate(objects, 1):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n".encode("ascii"))
            out.write(obj)
            out.write(b"\nendobj\n")

        xref_pos = out.tell()
        out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        out.write(b"0000000000 65535 f \n")
        for offset in offsets:
            out.write(f"{offset:010d} 00000 n \n".encode("ascii"))

        out.write(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
                "ascii"
            )
        )
        return out.getvalue()
