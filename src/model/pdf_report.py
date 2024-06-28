import pandas as pd
import os
from fpdf import FPDF
from datetime import datetime
from typing import AnyStr
from src.client.config_manager import ConfigManager
from src import logger

logthis = logger.setup_child_logger("pdf_report", __name__)


class PDFReport(FPDF):
    def __init__(
        self,
        config: ConfigManager,
        orientation="L",
        unit="mm",
        format="A4",
        date_format="%B %d %Y",
    ):
        super().__init__(orientation=orientation, unit=unit, format=format)
        self.date_format = date_format
        self.config = config.get_ui_config()

        self.add_font(
            self.config.get("FONT_NAME"), "", self.config.get("FONT_REGULAR_PATH")
        )
        self.add_font(
            self.config.get("FONT_NAME"), "B", self.config.get("FONT_BOLD_PATH")
        )

        self.table_headers = []
        self.column_widths = []

    def header(self):
        # Title in bold
        self.set_font(self.config.get("FONT_NAME"), "B", 24)
        self.cell(0, 10, self.config.get("HEADER_TEXT"), new_x="LMARGIN", new_y="NEXT")

        # Month/Year in light
        self.set_font(self.config.get("FONT_NAME"), "", 18)
        self.cell(
            0,
            10,
            datetime.now().strftime(self.date_format),
            new_x="LMARGIN",
            new_y="NEXT",
        )

        if self.page_no() > 1:
            self.add_table_header()

    def add_table_header(self):
        self.set_y(30)
        self.set_font(self.config.get("FONT_NAME"), "B", 11)
        for header, width in zip(self.table_headers, self.column_widths):
            self.cell(width, 10, header, border=1, align="C")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.config.get("FONT_NAME"), "", 6)
        self.set_text_color(175, 175, 175)
        footer_text = f"{self.config.get('FOOTER_TEXT')} | Page " + str(self.page_no())
        self.cell(0, 10, footer_text, 0, 0, "R")

    def export_excel_to_pdf(
        self, excel_file: AnyStr, date_format: AnyStr = "%B %d %Y"
    ) -> None:
        """
        Creates a PDF report from an Excel file containing patch data.

        :param excel_file: Path to the Excel file to convert to PDF.
        :type excel_file: AnyStr
        :param date_format: The date format string for the PDF report header.
        :type date_format: AnyStr
        """
        try:
            # Read excel file
            df = pd.read_excel(excel_file)

            # Create instance of FPDF
            pdf = PDFReport(date_format=date_format)
            pdf.table_headers = df.columns
            pdf.column_widths = [75, 40, 40, 40, 40, 40]
            pdf.add_page()
            pdf.add_table_header()

            # Data rows
            pdf.set_font(self.config.get("FONT_NAME"), "", 9)
            for index, row in df.iterrows():
                for data, width in zip(row, pdf.column_widths):
                    pdf.cell(width, 10, str(data), border=1, align="C")
                pdf.ln(10)

            # Save PDF to a file
            pdf_filename = os.path.splitext(excel_file)[0] + ".pdf"
            pdf.output(pdf_filename)

        except Exception as e:
            logthis.error(f"Error occurred trying to export PDF: {e}")