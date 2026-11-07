"""Windows 11 Microsoft Excel Grocery Bill Generator, Live Editor & Tamil/Tanglish Q&A Engine.

Capabilities:
1. Generates a realistic 25-item Indian Supermarket / Kirana Grocery Bill CSV
   (`grocery_bill_sheet.csv`) with exact quantities, unit prices, GST %, GST Rs.,
   line totals, categories, and payment modes.
2. Launches the sheet directly in Microsoft Excel
   (`C:\\Program Files\\Microsoft Office\\Root\\Office16\\EXCEL.EXE`) on the
   interactive Windows 11 desktop.
3. Computes 100% exact ground-truth math in Python (Grand Total, Subtotal, Total GST,
   Category breakdowns, Highest/Lowest priced items, Item lookups) so the LLM
   never guesses arithmetic on the Excel sheet.
4. Answers any spoken question about the Excel sheet in Tamil (`ta-IN`), Tanglish
   (Tamil + English code-mix), Telugu/Tenglish (`te-IN`), Hindi/Hinglish (`hi-IN`/`hi-en`),
   or English (`en-IN`), and supports voice edits (`add`, `update qty/price`, `remove`, `undo`).
"""

from __future__ import annotations

import contextlib
import csv
import ctypes
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EXCEL_DEFAULT_PATHS = (
    r"C:\Program Files\Microsoft Office\Root\Office16\EXCEL.EXE",
    r"C:\Program Files (x86)\Microsoft Office\Root\Office16\EXCEL.EXE",
)


@dataclass
class GroceryBillRow:
    item_id: str
    item_name: str
    tamil_alias: str
    category: str
    qty: float
    unit: str
    unit_price_inr: float
    gst_pct: float
    gst_amount_inr: float
    total_amount_inr: float
    payment_mode: str


def _make_row(
    idx: int,
    name: str,
    tamil_alias: str,
    category: str,
    qty: float,
    unit: str,
    unit_price: float,
    gst_pct: float,
    payment_mode: str = "UPI",
) -> GroceryBillRow:
    subtotal = round(qty * unit_price, 2)
    gst_amt = round(subtotal * (gst_pct / 100.0), 2)
    total = round(subtotal + gst_amt, 2)
    return GroceryBillRow(
        item_id=f"GRC-{idx:03d}",
        item_name=name,
        tamil_alias=tamil_alias,
        category=category,
        qty=qty,
        unit=unit,
        unit_price_inr=unit_price,
        gst_pct=gst_pct,
        gst_amount_inr=gst_amt,
        total_amount_inr=total,
        payment_mode=payment_mode,
    )


DEFAULT_GROCERY_ITEMS: List[GroceryBillRow] = [
    _make_row(
        1, "Ponni Boiled Rice", "பொன்னி அரிசி (arisi)", "Grains & Staples", 10.0, "kg", 62.0, 5.0
    ),
    _make_row(2, "Idli Rice", "இட்லி அரிசி", "Grains & Staples", 5.0, "kg", 54.0, 5.0),
    _make_row(
        3,
        "Toor Dal (Thuvaram Paruppu)",
        "துவரம் பருப்பு (paruppu)",
        "Grains & Staples",
        2.0,
        "kg",
        145.0,
        5.0,
    ),
    _make_row(
        4, "Urad Dal (Ulutham Paruppu)", "உளுத்தம் பருப்பு", "Grains & Staples", 1.0, "kg", 138.0, 5.0
    ),
    _make_row(
        5, "Chana Dal (Kadalai Paruppu)", "கடலை பருப்பு", "Grains & Staples", 1.0, "kg", 96.0, 5.0
    ),
    _make_row(
        6,
        "Aashirvaad Whole Wheat Atta",
        "கோதுமை மாவு (godhumai)",
        "Grains & Staples",
        5.0,
        "kg",
        58.0,
        5.0,
    ),
    _make_row(
        7,
        "Gold Winner Sunflower Oil",
        "சமையல் எண்ணெய் (ennai / oil)",
        "Cooking Oils & Ghee",
        2.0,
        "L",
        135.0,
        5.0,
    ),
    _make_row(
        8,
        "Idhayam Gingelly (Sesame) Oil",
        "நல்லெண்ணெய் (nallenai)",
        "Cooking Oils & Ghee",
        1.0,
        "L",
        340.0,
        5.0,
        "Card",
    ),
    _make_row(
        9,
        "Aavin Pure Cow Ghee",
        "ஆவின் நெய் (ney / ghee)",
        "Cooking Oils & Ghee",
        0.5,
        "L",
        640.0,
        12.0,
        "Card",
    ),
    _make_row(
        10,
        "Parachute Coconut Oil",
        "தேங்காய் எண்ணெய்",
        "Cooking Oils & Ghee",
        0.5,
        "L",
        190.0,
        5.0,
    ),
    _make_row(
        11,
        "Aavin Full Cream Milk",
        "ஆவின் பால் (paal / milk)",
        "Dairy & Beverages",
        4.0,
        "L",
        60.0,
        0.0,
        "Cash",
    ),
    _make_row(
        12,
        "Aavin Fresh Curd (Thayir)",
        "தயிர் (thayir / curd)",
        "Dairy & Beverages",
        1.0,
        "kg",
        75.0,
        5.0,
        "Cash",
    ),
    _make_row(
        13,
        "Bru Filter Coffee Powder",
        "பில்டர் காபி தூள் (coffee)",
        "Dairy & Beverages",
        0.5,
        "kg",
        460.0,
        5.0,
        "Card",
    ),
    _make_row(
        14, "3 Roses Tea Dust", "டீ தூள் (tea)", "Dairy & Beverages", 0.5, "kg", 320.0, 5.0
    ),
    _make_row(
        15,
        "Refined White Sugar (Sarkarai)",
        "சர்க்கரை (sarkarai / sugar)",
        "Grains & Staples",
        2.0,
        "kg",
        44.0,
        5.0,
        "Cash",
    ),
    _make_row(
        16,
        "Organic Jaggery (Vellam)",
        "வெல்லம் (vellam / jaggery)",
        "Grains & Staples",
        1.0,
        "kg",
        78.0,
        0.0,
        "Cash",
    ),
    _make_row(
        17,
        "Sakthi Sambar Powder",
        "சாம்பார் பொடி (sambar podi)",
        "Spices & Masalas",
        0.2,
        "kg",
        280.0,
        5.0,
    ),
    _make_row(
        18,
        "Sakthi Rasam Powder",
        "ரசம் பொடி (rasam podi)",
        "Spices & Masalas",
        0.1,
        "kg",
        310.0,
        5.0,
    ),
    _make_row(
        19,
        "Erode Turmeric Powder (Manjal)",
        "மஞ்சள் தூள் (manjal)",
        "Spices & Masalas",
        0.2,
        "kg",
        220.0,
        5.0,
    ),
    _make_row(
        20,
        "Tamarind (Puli)",
        "புளி (puli / tamarind)",
        "Spices & Masalas",
        0.5,
        "kg",
        160.0,
        0.0,
        "Cash",
    ),
    _make_row(
        21,
        "Small Onions (Chinna Vengayam)",
        "சின்ன வெங்காயம் (vengayam / onion)",
        "Vegetables & Fruits",
        3.0,
        "kg",
        65.0,
        0.0,
        "Cash",
    ),
    _make_row(
        22,
        "Ripe Tomatoes (Thakkali)",
        "தக்காளி (thakkali / tomato)",
        "Vegetables & Fruits",
        2.0,
        "kg",
        40.0,
        0.0,
        "Cash",
    ),
    _make_row(
        23,
        "Ooty Potatoes (Urulaikizhangu)",
        "உருளைக்கிழங்கு (potato)",
        "Vegetables & Fruits",
        2.0,
        "kg",
        48.0,
        0.0,
        "Cash",
    ),
    _make_row(
        24,
        "Surf Excel Matic Liquid",
        "சர்ப் எக்செல் (detergent)",
        "Household & Cleaning",
        1.0,
        "L",
        215.0,
        18.0,
        "Card",
    ),
    _make_row(
        25,
        "Vim Dishwash Gel",
        "விம் லிக்விட் (vim)",
        "Household & Cleaning",
        0.5,
        "L",
        190.0,
        18.0,
        "Card",
    ),
]


class ExcelGroceryController:
    """Manages the live Excel Grocery Bill CSV, launches EXCEL.EXE, and answers Tanglish Q&A."""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.csv_path = self.workspace_root / "grocery_bill_sheet.csv"
        self.rows: List[GroceryBillRow] = [
            GroceryBillRow(**asdict(r)) for r in DEFAULT_GROCERY_ITEMS
        ]
        self._history_stack: List[List[GroceryBillRow]] = []
        self.write_csv()

    def _find_excel_binary(self) -> Optional[str]:
        for candidate in EXCEL_DEFAULT_PATHS:
            if os.path.exists(candidate):
                return candidate
        return shutil.which("excel.exe")

    def write_csv(self) -> Path:
        """Write current `self.rows` plus a summary footer row to `grocery_bill_sheet.csv`."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh_ctx = self.csv_path.open("w", newline="", encoding="utf-8-sig")
        except PermissionError:
            # File is currently open and locked by Microsoft Excel (EXCEL.EXE)
            return self.csv_path
        with fh_ctx as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "Item_ID",
                    "Item_Name",
                    "Tamil_Name",
                    "Category",
                    "Qty",
                    "Unit",
                    "Unit_Price_INR",
                    "GST_Pct",
                    "GST_Amount_INR",
                    "Total_Amount_INR",
                    "Payment_Mode",
                ]
            )
            for r in self.rows:
                writer.writerow(
                    [
                        r.item_id,
                        r.item_name,
                        r.tamil_alias,
                        r.category,
                        r.qty,
                        r.unit,
                        f"{r.unit_price_inr:.2f}",
                        f"{r.gst_pct:.1f}%",
                        f"{r.gst_amount_inr:.2f}",
                        f"{r.total_amount_inr:.2f}",
                        r.payment_mode,
                    ]
                )
            analytics = self.compute_exact_analytics()
            writer.writerow([])
            writer.writerow(
                [
                    "TOTAL",
                    f"{analytics['total_items']} Items",
                    "மொத்த பில் (Grand Total)",
                    "",
                    analytics["total_quantity_units"],
                    "",
                    f"Subtotal: Rs.{analytics['subtotal_inr']:.2f}",
                    "",
                    f"Rs.{analytics['total_gst_inr']:.2f}",
                    f"Rs.{analytics['grand_total_inr']:.2f}",
                    "",
                ]
            )
        return self.csv_path

    def open_in_microsoft_excel(self) -> Dict[str, Any]:
        """Open Grocery Bill in Microsoft Excel and build live Category & Payment Charts via COM."""
        self.write_csv()
        analytics = self.compute_exact_analytics()
        charts_created = 0
        com_used = False

        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = True
            excel.DisplayAlerts = False

            wb = excel.Workbooks.Add()
            ws = wb.ActiveSheet
            ws.Name = "Grocery_BI_Dashboard"

            headers = [
                "Item_ID",
                "Item_Name",
                "Tamil_Name",
                "Category",
                "Qty",
                "Unit",
                "Unit_Price_INR",
                "GST_Pct",
                "GST_Amount_INR",
                "Total_Amount_INR",
                "Payment_Mode",
            ]
            for col_idx, h_text in enumerate(headers, start=1):
                cell = ws.Cells(1, col_idx)
                cell.Value = h_text
                cell.Font.Bold = True
                cell.Interior.Color = 0x8A3A1E  # Classic Navy/Royal Blue BGR
                cell.Font.Color = 0xFFFFFF

            for row_idx, r in enumerate(self.rows, start=2):
                ws.Cells(row_idx, 1).Value = r.item_id
                ws.Cells(row_idx, 2).Value = r.item_name
                ws.Cells(row_idx, 3).Value = r.tamil_alias
                ws.Cells(row_idx, 4).Value = r.category
                ws.Cells(row_idx, 5).Value = r.qty
                ws.Cells(row_idx, 6).Value = r.unit
                ws.Cells(row_idx, 7).Value = r.unit_price_inr
                ws.Cells(row_idx, 8).Value = r.gst_pct
                ws.Cells(row_idx, 9).Value = r.gst_amount_inr
                ws.Cells(row_idx, 10).Value = r.total_amount_inr
                ws.Cells(row_idx, 11).Value = r.payment_mode

            # Write Category Summary Table in columns M & N for live Chart 1
            ws.Cells(1, 13).Value = "Category"
            ws.Cells(1, 14).Value = "Revenue_INR"
            ws.Cells(1, 13).Font.Bold = True
            ws.Cells(1, 14).Font.Bold = True
            cat_items = list(analytics["category_totals"].items())
            for idx, (cat_name, cat_val) in enumerate(cat_items, start=2):
                ws.Cells(idx, 13).Value = cat_name
                ws.Cells(idx, 14).Value = cat_val

            # Write Payment Mode Summary Table in columns P & Q for live Chart 2
            ws.Cells(1, 16).Value = "Payment_Mode"
            ws.Cells(1, 17).Value = "Amount_INR"
            ws.Cells(1, 16).Font.Bold = True
            ws.Cells(1, 17).Font.Bold = True
            pay_items = list(analytics["payment_totals"].items())
            for idx, (pay_name, pay_val) in enumerate(pay_items, start=2):
                ws.Cells(idx, 16).Value = pay_name
                ws.Cells(idx, 17).Value = pay_val

            ws.Columns.AutoFit()

            # Embed Chart 1: Category Revenue Column Chart (xlColumnClustered = 51)
            chart_objs = ws.ChartObjects()
            ch1 = chart_objs.Add(560, 20, 440, 240)
            ch1.Chart.SetSourceData(ws.Range(f"M1:N{len(cat_items) + 1}"))
            ch1.Chart.ChartType = 51
            ch1.Chart.HasTitle = True
            ch1.Chart.ChartTitle.Text = (
                f"Grocery Revenue by Category (Grand Total: Rs.{analytics['grand_total_inr']:.0f})"
            )
            charts_created += 1

            # Embed Chart 2: Payment Mode Pie Chart (xlPie = 5)
            ch2 = chart_objs.Add(560, 275, 440, 220)
            ch2.Chart.SetSourceData(ws.Range(f"P1:Q{len(pay_items) + 1}"))
            ch2.Chart.ChartType = 5
            ch2.Chart.HasTitle = True
            ch2.Chart.ChartTitle.Text = "Payment Mode Split (UPI vs Card vs Cash)"
            charts_created += 1

            xlsx_path = self.workspace_root / "grocery_bi_dashboard.xlsx"
            with contextlib.suppress(Exception):
                wb.SaveAs(str(xlsx_path))
            with contextlib.suppress(Exception):
                excel.WindowState = -4137  # xlMaximized
                eh = int(excel.Hwnd)
                cur_tid = kernel32.GetCurrentThreadId()
                fg_hwnd = int(user32.GetForegroundWindow() or 0)
                fg_tid = (
                    user32.GetWindowThreadProcessId(ctypes.c_void_p(fg_hwnd), None)
                    if fg_hwnd
                    else 0
                )
                if fg_tid and cur_tid != fg_tid:
                    user32.AttachThreadInput(cur_tid, fg_tid, True)
                user32.ShowWindow(ctypes.c_void_p(eh), 3)
                user32.BringWindowToTop(ctypes.c_void_p(eh))
                user32.SetForegroundWindow(ctypes.c_void_p(eh))
                user32.SwitchToThisWindow(ctypes.c_void_p(eh), True)
                if fg_tid and cur_tid != fg_tid:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
            com_used = True
        except Exception:
            excel_bin = self._find_excel_binary()
            if excel_bin:
                with contextlib.suppress(Exception):
                    subprocess.Popen(  # noqa: S603
                        [excel_bin, str(self.csv_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

        return {
            "status": "COMPLETED",
            "csv_path": str(self.csv_path),
            "com_automation_used": com_used,
            "charts_created_in_excel": charts_created,
            "analytics": analytics,
        }

    def compute_exact_analytics(self) -> Dict[str, Any]:
        """Compute 100% exact Python ground-truth statistics over the grocery bill."""
        if not self.rows:
            return {
                "total_items": 0,
                "total_quantity_units": 0.0,
                "subtotal_inr": 0.0,
                "total_gst_inr": 0.0,
                "grand_total_inr": 0.0,
                "highest_item": None,
                "lowest_item": None,
                "category_totals": {},
                "payment_totals": {},
                "rows": [],
            }

        total_gst = round(sum(r.gst_amount_inr for r in self.rows), 2)
        grand_total = round(sum(r.total_amount_inr for r in self.rows), 2)
        subtotal = round(grand_total - total_gst, 2)
        total_qty = round(sum(r.qty for r in self.rows), 2)

        highest = max(self.rows, key=lambda r: r.total_amount_inr)
        lowest = min(self.rows, key=lambda r: r.total_amount_inr)

        cat_totals: Dict[str, float] = {}
        pay_totals: Dict[str, float] = {}
        pay_gst_totals: Dict[str, float] = {}
        for r in self.rows:
            cat_totals[r.category] = round(cat_totals.get(r.category, 0.0) + r.total_amount_inr, 2)
            pay_totals[r.payment_mode] = round(
                pay_totals.get(r.payment_mode, 0.0) + r.total_amount_inr, 2
            )
            pay_gst_totals[r.payment_mode] = round(
                pay_gst_totals.get(r.payment_mode, 0.0) + r.gst_amount_inr, 2
            )

        low_stock = sorted(self.rows, key=lambda r: r.qty)[:4]

        return {
            "csv_path": str(self.csv_path),
            "total_items": len(self.rows),
            "total_quantity_units": total_qty,
            "subtotal_inr": subtotal,
            "total_gst_inr": total_gst,
            "grand_total_inr": grand_total,
            "highest_item": {
                "name": highest.item_name,
                "tamil_alias": highest.tamil_alias,
                "qty": highest.qty,
                "unit": highest.unit,
                "total_inr": highest.total_amount_inr,
            },
            "lowest_item": {
                "name": lowest.item_name,
                "tamil_alias": lowest.tamil_alias,
                "qty": lowest.qty,
                "unit": lowest.unit,
                "total_inr": lowest.total_amount_inr,
            },
            "category_totals": cat_totals,
            "payment_totals": pay_totals,
            "payment_gst_totals": pay_gst_totals,
            "low_stock_items": [
                {"name": r.item_name, "qty": r.qty, "unit": r.unit, "category": r.category}
                for r in low_stock
            ],
            "rows": [asdict(r) for r in self.rows],
        }

    def add_or_update_item(
        self,
        item_name: str,
        qty: float,
        unit: str = "kg",
        unit_price_inr: float = 60.0,
        gst_pct: float = 5.0,
        category: str = "Grains & Staples",
    ) -> Dict[str, Any]:
        """Add a new grocery row or update an existing matching item, saving to CSV."""
        self._history_stack.append([GroceryBillRow(**asdict(r)) for r in self.rows])
        low_target = item_name.strip().lower()
        for idx, r in enumerate(self.rows):
            if low_target in r.item_name.lower() or low_target in r.tamil_alias.lower():
                updated = _make_row(
                    idx + 1,
                    r.item_name,
                    r.tamil_alias,
                    r.category,
                    qty,
                    r.unit,
                    r.unit_price_inr,
                    r.gst_pct,
                    r.payment_mode,
                )
                self.rows[idx] = updated
                self.write_csv()
                return {
                    "action": "updated",
                    "row": asdict(updated),
                    "analytics": self.compute_exact_analytics(),
                }

        new_row = _make_row(
            len(self.rows) + 1,
            item_name.strip().title(),
            item_name.strip(),
            category,
            qty,
            unit,
            unit_price_inr,
            gst_pct,
            "UPI",
        )
        self.rows.append(new_row)
        self.write_csv()
        return {
            "action": "added",
            "row": asdict(new_row),
            "analytics": self.compute_exact_analytics(),
        }

    def undo_last_edit(self) -> bool:
        if not self._history_stack:
            return False
        self.rows = self._history_stack.pop()
        self.write_csv()
        return True

    def build_ground_truth_prompt_context(self) -> str:
        """Build a compact, 100% verified ground-truth Excel summary for the LLM prompt."""
        a = self.compute_exact_analytics()
        hi = a["highest_item"] or {}
        lo = a["lowest_item"] or {}
        cat_str = ", ".join(f"{k}: Rs.{v:.0f}" for k, v in a["category_totals"].items())
        pay_str = ", ".join(f"{k}: Rs.{v:.0f}" for k, v in a["payment_totals"].items())
        item_lines = [
            f"- {r.item_name} ({r.tamil_alias}): {r.qty:g}{r.unit} @ Rs.{r.unit_price_inr:g} "
            f"+ {r.gst_pct:g}% GST = Rs.{r.total_amount_inr:.2f} [{r.category}, {r.payment_mode}]"
            for r in self.rows
        ]
        return (
            f"VERIFIED EXCEL GROCERY BILL GROUND TRUTH ({a['csv_path']}):\n"
            f"- Total Items: {a['total_items']}\n"
            f"- Subtotal (Before GST): Rs.{a['subtotal_inr']:.2f}\n"
            f"- Total GST Amount: Rs.{a['total_gst_inr']:.2f}\n"
            f"- GRAND TOTAL BILL: Rs.{a['grand_total_inr']:.2f}\n"
            f"- Most Expensive Item: {hi.get('name')} ({hi.get('qty')}{hi.get('unit')}) "
            f"= Rs.{hi.get('total_inr')}\n"
            f"- Cheapest Item: {lo.get('name')} ({lo.get('qty')}{lo.get('unit')}) "
            f"= Rs.{lo.get('total_inr')}\n"
            f"- Category Breakdown: {cat_str}\n"
            f"- Payment Mode Breakdown: {pay_str}\n"
            "All 25 Items in Sheet:\n" + "\n".join(item_lines)
        )

    def build_paint_revenue_chart_scene_spec(self) -> Any:
        """Build a proportional 4-bar MS Paint `SceneSpec` chart from live Excel category totals."""
        from src.windows_integration.paint_controller import SceneObjectSpec, SceneSpec

        a = self.compute_exact_analytics()
        cats = list(a["category_totals"].items())[:4]
        max_val = max((v for _, v in cats), default=1.0)
        palette_cycle = ["indigo", "red", "green", "gold"]
        objs: List[SceneObjectSpec] = []

        for idx, (cat_name, val) in enumerate(cats):
            bar_h = round(max(0.10, min(0.68, (val / max_val) * 0.65)), 4)
            bar_x = round(0.14 + idx * 0.20, 4)
            bar_y = round(0.82 - bar_h, 4)
            safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", cat_name.lower())[:20].strip("_")
            objs.append(
                SceneObjectSpec(
                    id=f"bar_{idx + 1}_{safe_id}",
                    shape="rectangle",
                    x=bar_x,
                    y=bar_y,
                    w=0.13,
                    h=bar_h,
                    fill_color=palette_cycle[idx % len(palette_cycle)],
                    outline_color="black",
                )
            )
        return SceneSpec(scene_id="excel_revenue_chart", clear_before_draw=True, objects=objs)

    def ensure_excel_visible_in_foreground(self) -> bool:
        """If Microsoft Excel (`XLMAIN`) is running, bring it to foreground; otherwise launch it."""
        try:
            import uiautomation as auto

            with auto.UIAutomationInitializerInThread():
                ewin = auto.WindowControl(searchDepth=2, ClassName="XLMAIN")
                if ewin.Exists(maxSearchSeconds=0.8):
                    eh = int(ewin.NativeWindowHandle or 0)
                    if eh:
                        cur_tid = kernel32.GetCurrentThreadId()
                        fg_hwnd = int(user32.GetForegroundWindow() or 0)
                        fg_tid = (
                            user32.GetWindowThreadProcessId(ctypes.c_void_p(fg_hwnd), None)
                            if fg_hwnd
                            else 0
                        )
                        if fg_tid and cur_tid != fg_tid:
                            user32.AttachThreadInput(cur_tid, fg_tid, True)
                        user32.ShowWindow(ctypes.c_void_p(eh), 9)
                        user32.ShowWindow(ctypes.c_void_p(eh), 3)
                        user32.BringWindowToTop(ctypes.c_void_p(eh))
                        user32.SetForegroundWindow(ctypes.c_void_p(eh))
                        user32.SwitchToThisWindow(ctypes.c_void_p(eh), True)
                        if fg_tid and cur_tid != fg_tid:
                            user32.AttachThreadInput(cur_tid, fg_tid, False)
                        with contextlib.suppress(Exception):
                            ewin.SetActive()
                        return True
        except Exception:
            pass
        self.open_in_microsoft_excel()
        return True

    def answer_question_deterministically_or_context(
        self,
        question: str,
        reply_language: str = "ta-IN",
    ) -> Tuple[str, Dict[str, Any]]:
        """Compute exact answer from the Excel sheet in Tamil/Telugu/Hindi/English & show Excel."""
        low = (question or "").strip().lower()
        # Always bring the Excel window to the foreground so the user sees the sheet & charts!
        self.ensure_excel_visible_in_foreground()
        a = self.compute_exact_analytics()
        grand = a["grand_total_inr"]
        sub = a["subtotal_inr"]
        gst = a["total_gst_inr"]
        cnt = a["total_items"]
        hi = a["highest_item"]
        lo = a["lowest_item"]
        cats = a["category_totals"]
        pays = a["payment_totals"]

        # 1. Check if the user asked to open/generate the Excel grocery bill or BI dashboard
        open_keys = (
            "open excel",
            "open grocery",
            "ఓపెన్ గ్రోసరీ",
            "గ్రోసరీ ఓపెన్",
            "ఎక్సల్ ఓపెన్",
            "ఎక్సెల్ ఓపెన్",
            "ఎక్సెల్ ఫైల్ ఓపెన్",
            "ఎక్సల్ ఫైల్ ఓపెన్",
            "generate",
            "grocery bill open",
            "excel open",
            "sheet open",
            "bi dashboard",
            "power bi",
            "chart",
            "dashboard",
        )
        if any(k in low for k in open_keys):
            if reply_language == "te-IN":
                msg = (
                    f"ఇదిగో మీ ఎక్సెల్ గ్రోసరీ బిల్ మరియు చార్ట్స్ ఓపెన్ చేశాను! మొత్తం {cnt} వస్తువులు ఉన్నాయి, "
                    f"గ్రాండ్ టోటల్ {grand:.0f} రూపాయలు (GST {gst:.0f} రూపాయలు)."
                )
            elif reply_language == "ta-IN":
                msg = (
                    f"எக்செல் மளிகை பில் திறக்கப்பட்டது! மொத்தம் {cnt} பொருட்கள், "
                    f"மொத்த பில் தொகை {grand:.0f} ரூபாய்."
                )
            else:
                msg = (
                    f"Excel grocery bill opened with {cnt} items! "
                    f"Grand total including GST is Rs.{grand:.2f}."
                )
            return msg, a

        # 2. Stock / Restock / Inventory improvement question ('ఏ స్టాక్ ని ఇంప్రూవ్ చేసుకోవాలి', 'stock', 'restock')
        stock_keys = ("stock", "స్టాక్", "ఇంప్రూవ్", "improve", "restock", "తక్కువ క్వాంటిటీ", "inventory")
        if any(k in low for k in stock_keys):
            low_items = a.get("low_stock_items") or []
            low_str = ", ".join(f"{it['name']} ({it['qty']:g} {it['unit']})" for it in low_items[:3])
            if reply_language == "te-IN":
                return (
                    f"ఎక్సెల్ షీట్ ప్రకారం తక్కువ స్టాక్ ఉన్న వస్తువులు: {low_str}. "
                    "మసాలా పొడులు (Spices) మరియు డైరీ వస్తువుల స్టాక్ వెంటనే పెంచుకోవాలి, అలాగే ఎక్కువ డిమాండ్ ఉన్న Ponni Rice మరియు Sunflower Oil రీ-స్టాక్ చేయండి.",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"குறைந்த ஸ்டாக் உள்ள பொருட்கள்: {low_str}. மசாலா மற்றும் அரிசி வகைகளை உடனே ரீ-ஸ்டாக் செய்யவும்.",
                    a,
                )
            return (
                f"Lowest stock items in Excel: {low_str}. You should restock Spices & Masalas and high-demand Grains & Staples.",
                a,
            )

        # 3. Combined UPI + GST question ('యూపీఐకి జీఎస్టీ పడిందా', 'upi gst')
        gst_keys = ("gst", "tax", "வரி", "జిఎస్టి", "జీఎస్టీ", "టాక్స్", "जीएसटी", "टैक्स")
        pay_keys = (
            "upi",
            "cash",
            "card",
            "payment",
            "பேமெண்ட்",
            "யுபிஐ",
            "యుపిఐ",
            "యూపీఐ",
            "పేమెంట్",
            "పేమెంట్స్",
            "కార్డ్",
            "క్యాష్",
            "నగదు",
            "यूपीआई",
            "पेमेंट",
            "कैश",
            "कार्ड",
        )
        has_gst = any(k in low for k in gst_keys)
        has_pay = any(k in low for k in pay_keys)
        if has_gst and has_pay:
            pay_gst = a.get("payment_gst_totals") or {}
            upi_gst = pay_gst.get("UPI", 0.0)
            card_gst = pay_gst.get("Card", 0.0)
            cash_gst = pay_gst.get("Cash", 0.0)
            upi_tot = pays.get("UPI", 0.0)
            if reply_language == "te-IN":
                return (
                    f"అవును, యూపీఐ (UPI) ద్వారా కొన్న 12 వస్తువులపై మొత్తం {upi_gst:.0f} రూపాయల జీఎస్టీ (GST) పడింది (UPI మొత్తం బిల్ {upi_tot:.0f} రూపాయలు). "
                    f"కార్డ్ పేమెంట్స్ పై {card_gst:.0f} రూపాయలు మరియు క్యాష్ పై {cash_gst:.0f} రూపాయల GST పడింది.",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"ஆம், UPI மூலம் வாங்கிய பொருட்களுக்கு {upi_gst:.0f} ரூபாய் GST வரி சேர்க்கப்பட்டுள்ளது (மொத்த UPI பில் {upi_tot:.0f} ரூபாய்).",
                    a,
                )
            return (
                f"Yes, UPI purchases included Rs.{upi_gst:.2f} in GST (out of Rs.{upi_tot:.2f} total UPI payments). "
                f"Card GST is Rs.{card_gst:.2f} and Cash GST is Rs.{cash_gst:.2f}.",
                a,
            )

        # 4. GST question ("gst", "tax", "வரி", "జిఎస్టి", "जीएसटी")
        if has_gst:
            if reply_language == "te-IN":
                return (
                    f"మొత్తం 25 వస్తువులపై జిఎస్టి (GST) పన్ను {gst:.0f} రూపాయలు. "
                    f"పన్నుకు ముందు బిల్ {sub:.0f} రూపాయలు, మొత్తం గ్రాండ్ టోటల్ {grand:.0f} రూపాయలు.",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"மொத்த பில்லில் ஜிஎஸ்டி (GST) வரி மட்டும் {gst:.0f} ரூபாய். "
                    f"வரிக்கு முன் தொகை {sub:.0f} ரூபாய், மொத்த பில் {grand:.0f} ரூபாய்.",
                    a,
                )
            if reply_language in ("hi-IN", "hi-en"):
                return (
                    f"कुल बिल में जीएसटी (GST) टैक्स {gst:.0f} रुपये है। "
                    f"टैक्स से पहले की राशि {sub:.0f} रुपये और कुल बिल {grand:.0f} रुपये है।",
                    a,
                )
            return (
                f"Total GST across all {cnt} items is Rs.{gst:.2f}. "
                f"Subtotal before tax is Rs.{sub:.2f} and Grand Total is Rs.{grand:.2f}.",
                a,
            )

        # 5. Payment mode question ("upi", "cash", "card", "payment", "యుపిఐ", "పేమెంట్", "यूपीआई")
        if has_pay:
            upi_v = pays.get("UPI", 0.0)
            card_v = pays.get("Card", 0.0)
            cash_v = pays.get("Cash", 0.0)
            upi_cnt = sum(1 for r in self.rows if r.payment_mode == "UPI")
            if reply_language == "te-IN":
                return (
                    f"యుపిఐ (UPI) ద్వారా {upi_cnt} వస్తువులకు మొత్తం {upi_v:.0f} రూపాయలు పేమెంట్ చేశారు. "
                    f"కార్డ్ ద్వారా {card_v:.0f} రూపాయలు మరియు క్యాష్ ద్వారా {cash_v:.0f} రూపాయలు చెల్లించారు.",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"பேமெண்ட் விவரம்: UPI மூலம் {upi_v:.0f} ரூபாய் ({upi_cnt} பொருட்கள்), "
                    f"Card மூலம் {card_v:.0f} ரூபாய், மற்றும் Cash மூலம் {cash_v:.0f} ரூபாய் செலுத்தப்பட்டுள்ளது.",
                    a,
                )
            if reply_language in ("hi-IN", "hi-en"):
                return (
                    f"यूपीआई (UPI) से {upi_cnt} आइटम के लिए कुल {upi_v:.0f} रुपये का पेमेंट हुआ है। "
                    f"कार्ड से {card_v:.0f} रुपये और कैश से {cash_v:.0f} रुपये दिए गए हैं।",
                    a,
                )
            return (
                f"Payment breakdown: UPI Rs.{upi_v:.2f} ({upi_cnt} items), Card Rs.{card_v:.2f}, "
                f"and Cash Rs.{cash_v:.2f} (Grand Total Rs.{grand:.2f}).",
                a,
            )

        # 2. Check if user asked about a specific Category (e.g. Grains & Staples / గ్రీన్స్ అండ్ స్టాప్స్)
        category_query_map = {
            "Grains & Staples": (
                "grains",
                "staples",
                "గ్రీన్స్",
                "గ్రెయిన్స్",
                "స్టాప్",
                "స్టాపుల్స్",
                "ధాన్యాలు",
                "अनाज",
                "தானியங்கள்",
            ),
            "Cooking Oils & Ghee": ("cooking oil", "oils", "నూనెలు", "तेल और घी", "எண்ணெய் வகைகள்"),
            "Dairy & Beverages": ("dairy", "beverages", "డైరీ", "పాల ఉత్పత్తులు", "डेयरी"),
            "Vegetables & Fruits": ("vegetables", "fruits", "కూరగాయలు", "सब्जियां", "காய்கறிகள்"),
            "Spices & Masalas": ("spices", "masala", "మసాలా", "मसाले", "மசாலா"),
            "Household & Cleaning": ("household", "cleaning", "క్లీనింగ్", "హౌస్ హోల్డ్"),
        }
        for cat_key, kw_tuple in category_query_map.items():
            if any(k in low for k in kw_tuple):
                cat_rows = [r for r in self.rows if r.category == cat_key]
                cat_total = cats.get(cat_key, 0.0)
                top_items = ", ".join(
                    f"{r.item_name} (Rs.{r.total_amount_inr:.0f})" for r in cat_rows[:4]
                )
                if reply_language == "te-IN":
                    return (
                        f"{cat_key} కేటగిరీలో మొత్తం {len(cat_rows)} వస్తువులు ఉన్నాయి, "
                        f"వాటి మొత్తం ధర {cat_total:.0f} రూపాయలు. ముఖ్యమైనవి: {top_items}.",
                        a,
                    )
                if reply_language == "ta-IN":
                    return (
                        f"{cat_key} பிரிவில் மொத்தம் {len(cat_rows)} பொருட்கள் உள்ளன, "
                        f"மொத்த தொகை {cat_total:.0f} ரூபாய். ముఖ్య பொருட்கள்: {top_items}.",
                        a,
                    )
                if reply_language in ("hi-IN", "hi-en"):
                    return (
                        f"{cat_key} कैटेगरी में कुल {len(cat_rows)} आइटम हैं और कुल राशि "
                        f"{cat_total:.0f} रुपये है ({top_items})।",
                        a,
                    )
                return (
                    f"In {cat_key}, there are {len(cat_rows)} items totaling Rs.{cat_total:.2f} "
                    f"(including {top_items}).",
                    a,
                )

        # 3. Check if user asked about specific item(s) in the grocery bill
        matched_rows: List[GroceryBillRow] = []
        item_search_keywords = {
            "rice": "rice",
            "arisi": "rice",
            "அரிசி": "rice",
            "బియ్యం": "rice",
            "రైస్": "rice",
            "चावल": "rice",
            "ponni": "ponni",
            "idli": "idli",
            "dal": "dal",
            "paruppu": "paruppu",
            "பருப்பு": "paruppu",
            "పప్పు": "dal",
            "దాల్": "dal",
            "दाल": "dal",
            "oil": "oil",
            "ennai": "oil",
            "எண்ணெய்": "oil",
            "నూనె": "oil",
            "ఆయిల్": "oil",
            "तेल": "oil",
            "ghee": "ghee",
            "ney": "ghee",
            "நெய்": "ghee",
            "నెయ్యి": "ghee",
            "ఘీ": "ghee",
            "घी": "ghee",
            "milk": "milk",
            "paal": "milk",
            "பால்": "milk",
            "పాలు": "milk",
            "మిల్క్": "milk",
            "दूध": "milk",
            "coffee": "coffee",
            "காபி": "coffee",
            "కాఫీ": "coffee",
            "कॉफी": "coffee",
            "tea": "tea",
            "டீ": "tea",
            "టీ": "tea",
            "चाय": "tea",
            "sugar": "sugar",
            "sarkarai": "sugar",
            "சர்க்கரை": "sugar",
            "చక్కెర": "sugar",
            "షుగర్": "sugar",
            "चीनी": "sugar",
            "onion": "onion",
            "vengayam": "onion",
            "வெங்காயம்": "onion",
            "ఉల్లిపాయ": "onion",
            "ఆనియన్": "onion",
            "प्याज": "onion",
            "tomato": "tomato",
            "thakkali": "tomato",
            "தக்காளி": "tomato",
            "టమాటా": "tomato",
            "टमाटर": "tomato",
            "potato": "potato",
            "detergent": "surf",
            "surf": "surf",
            "vim": "vim",
        }
        matched_terms = {
            target
            for kw, target in item_search_keywords.items()
            if re.search(rf"(?:^|\b|\s){re.escape(kw)}(?:\b|\s|$)", low)
        }
        if matched_terms:
            for r in self.rows:
                combined_name = f"{r.item_name} {r.tamil_alias}".lower()
                if any(t in combined_name for t in matched_terms):
                    matched_rows.append(r)

        if matched_rows:
            item_sum = round(sum(r.total_amount_inr for r in matched_rows), 2)
            names_str = ", ".join(
                f"{r.item_name} ({r.qty:g}{r.unit}: Rs.{r.total_amount_inr:.0f})"
                for r in matched_rows
            )
            if reply_language == "te-IN":
                return (
                    f"ఎక్సెల్ లెక్క ప్రకారం {names_str} — వీటి మొత్తం ధర {item_sum:.0f} రూపాయలు "
                    f"(మొత్తం బిల్ {grand:.0f} రూపాయలలో).",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"எக்செல் கணக்கின்படி {names_str} — இதன் மொத்த தொகை {item_sum:.0f} ரூபாய். "
                    f"மொத்த பில் தொகை {grand:.0f} ரூபாய்.",
                    a,
                )
            if reply_language in ("hi-IN", "hi-en"):
                return (
                    f"एक्सेल बिल के अनुसार {names_str} — इनका कुल योग {item_sum:.0f} रुपये है "
                    f"(कुल बिल {grand:.0f} रुपये में से)।",
                    a,
                )
            return (
                f"From the Excel sheet: {names_str} — combined total is Rs.{item_sum:.2f} "
                f"(out of Grand Total Rs.{grand:.2f}).",
                a,
            )

        # 4. Highest / most expensive item
        if any(
            k in low
            for k in (
                "expensive",
                "costly",
                "highest",
                "maximum",
                "adhiga",
                "athiga",
                "அதிக",
                "விலை",
                "ఎక్కువ ధర",
                "ఖరీదైన",
                "కాస్ట్లీ",
                "महंगा",
                "सबसे ज्यादा",
            )
        ) and hi:
            if reply_language == "te-IN":
                return (
                    f"ఈ బిల్లులో అత్యంత ఖరీదైన వస్తువు {hi['name']} ({hi['qty']:g} {hi['unit']}), "
                    f"దీని ధర {hi['total_inr']:.0f} రూపాయలు. అతి తక్కువ ధర వస్తువు {lo['name']} ({lo['total_inr']:.0f} రూపాయలు).",
                    a,
                )
            if reply_language == "ta-IN":
                return (
                    f"பில்லில் அதிக விலையுள்ள பொருள் {hi['name']} ({hi['qty']:g} {hi['unit']}), "
                    f"இதன் மொத்த விலை {hi['total_inr']:.0f} ரூபாய். "
                    f"குறைந்த விலை பொருள் {lo['name']} ({lo['total_inr']:.0f} ரூபாய்).",
                    a,
                )
            if reply_language in ("hi-IN", "hi-en"):
                return (
                    f"बिल में सबसे महंगा आइटम {hi['name']} ({hi['qty']:g} {hi['unit']}) है जिसकी कीमत "
                    f"{hi['total_inr']:.0f} रुपये है, और सबसे सस्ता {lo['name']} ({lo['total_inr']:.0f} रुपये) है।",
                    a,
                )
            return (
                f"The most expensive item on the Excel bill is {hi['name']} "
                f"({hi['qty']:g}{hi['unit']}) at Rs.{hi['total_inr']:.2f}, "
                f"and the cheapest is {lo['name']} at Rs.{lo['total_inr']:.2f}.",
                a,
            )

        # 6. Category breakdown or overall total summary (Tamil, Telugu, Hindi, English)
        staples_v = cats.get("Grains & Staples", 0.0)
        oils_v = cats.get("Cooking Oils & Ghee", 0.0)
        if reply_language == "ta-IN":
            return (
                f"இதோ உங்கள் எக்செல் மளிகை பில்! மொத்தம் {cnt} பொருட்கள் உள்ளன, "
                f"மொத்த தொகை {grand:.0f} ரூபாய் (GST {gst:.0f} ரூபாய்). "
                f"அரிசி மற்றும் பருப்பு {staples_v:.0f} ரூபாய், எண்ணெய் மற்றும் நெய் {oils_v:.0f} ரூபாய்.",
                a,
            )
        if reply_language == "te-IN":
            return (
                f"ఇదిగో మీ ఎక్సెల్ గ్రోసరీ బిల్! మొత్తం {cnt} వస్తువులు ఉన్నాయి, "
                f"మొత్తం బిల్ {grand:.0f} రూపాయలు (GST {gst:.0f} రూపాయలు). "
                f"బియ్యం మరియు పప్పులు {staples_v:.0f} రూపాయలు, నూనె మరియు నెయ్యి {oils_v:.0f} రూపాయలు.",
                a,
            )
        if reply_language in ("hi-IN", "hi-en"):
            return (
                f"यह रहा आपका एक्सेल ग्रोसरी बिल! कुल {cnt} आइटम हैं और कुल बिल {grand:.0f} रुपये है "
                f"(जिसमें {gst:.0f} रुपये GST शामिल है)। अनाज और दालें {staples_v:.0f} रुपये, तेल और घी {oils_v:.0f} रुपये।",
                a,
            )
        return (
            f"Here is your Excel grocery bill! There are {cnt} items with a Grand Total of "
            f"Rs.{grand:.2f} (including Rs.{gst:.2f} GST). "
            f"Grains & Staples total Rs.{staples_v:.2f} and Oils & Ghee total Rs.{oils_v:.2f}.",
            a,
        )

    async def answer_question_dynamically_with_llm(
        self,
        question: str,
        llm_provider: Any,
        reply_language: str = "te-IN",
    ) -> Tuple[str, Dict[str, Any]]:
        """Answer ANY open-ended question about the Excel sheet dynamically via LLM + Python ground truth."""
        import asyncio
        from src.providers.base import ChatMessage

        det_msg, analytics = await asyncio.to_thread(
            self.answer_question_deterministically_or_context,
            question,
            reply_language,
        )
        low = (question or "").strip().lower()
        # If the deterministic matcher hit any standard rule (open/grocery/bill/GST/payment/stock/items), return immediately (<10ms)
        specific_triggers = (
            "open",
            "ఓపెన్",
            "grocery",
            "గ్రోసరీ",
            "గ్రాసరీ",
            "ఎక్సెల్",
            "ఎక్సల్",
            "బిల్",
            "బిల్లు",
            "gst",
            "జిఎస్టి",
            "జీఎస్టీ",
            "upi",
            "యుపిఐ",
            "యూపీఐ",
            "యూకే పేమెంట్స్",
            "పేమెంట్",
            "పేమెంట్స్",
            "స్టాక్",
            "stock",
            "గ్రీన్స్",
            "గ్రెయిన్స్",
            "స్టాప్స్",
            "arisi",
            "బియ్యం",
            "oil",
            "నూనె",
            "ఖరీదైన",
            "ఎక్కువ ధర",
            "costly",
            "expensive",
        )
        if llm_provider is None or any(t in low for t in specific_triggers):
            return det_msg, analytics

        # Otherwise ask the LLM with the 100% verified 25-row Excel ground truth
        ground_truth = self.build_ground_truth_prompt_context()
        sys_prompt = (
            "You are Rahul, a funny, witty, energetic male voice assistant analyzing a live Microsoft Excel "
            "Grocery Bill on the user's screen.\n"
            f"Reply in language `{reply_language}` using the matching script (Telugu script for te-IN, "
            "Tamil script for ta-IN, Devanagari for hi-IN, English for en-IN).\n"
            "Keep your answer to 1 or 2 short, lively, playful spoken sentences (like a fun buddy/boss assistant) "
            "using ONLY the exact numbers from the verified Excel ground truth below. Use digits for numbers. No markdown.\n\n"
            f"{ground_truth}"
        )
        try:
            resp = await asyncio.wait_for(
                llm_provider.complete(
                    messages=[
                        ChatMessage(role="system", content=sys_prompt),
                        ChatMessage(role="user", content=question),
                    ],
                    temperature=0.2,
                    max_tokens=180,
                ),
                timeout=6.0,
            )
            content = (getattr(resp, "content", "") if not isinstance(resp, str) else resp).strip()
            if content and "{" not in content:
                return content, analytics
        except Exception:
            pass
        return det_msg, analytics
