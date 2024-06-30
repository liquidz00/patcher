import os
from click import echo, style
from datetime import datetime, timedelta
from typing import AnyStr, Optional
from threading import Event

from src import exceptions, logger, utils
from src.logger import LogMe
from src.client.config_manager import ConfigManager
from src.client.ui_manager import UIConfigManager
from src.client.token_manager import TokenManager
from src.client.api_client import ApiClient
from src.model.excel_report import ExcelReport
from src.model.pdf_report import PDFReport
from src.utils import check_token


class Patcher:
    """Main class for managing the patch reporting process in the Patcher application."""

    def __init__(
        self,
        config: ConfigManager,
        token_manager: TokenManager,
        api_client: ApiClient,
        excel_report: ExcelReport,
        pdf_report: PDFReport,
        ui_config: UIConfigManager,
        debug=False,
    ):
        """
        Initializes the Patcher class with the provided components.

        :param config: Instance of ConfigManager for managing configuration.
        :type config: ConfigManager
        :param token_manager: Instance of TokenManager for managing tokens.
        :type token_manager: TokenManager
        :param api_client: Instance of ApiClient for interacting with the Jamf API.
        :type api_client: ApiClient
        :param excel_report: Instance of ExcelReport for generating Excel reports.
        :type excel_report: ExcelReport
        :param pdf_report: Instance of PDFReport for generating PDF reports.
        :type pdf_report: PDFReport
        :param ui_config: Instance of UIConfigManager for UI configuration.
        :type ui_config: UIConfigManager
        :param debug: Enable or disable debug mode. Defaults to False.
        :type debug: bool
        """
        self.config = config
        self.token_manager = token_manager
        self.api_client = api_client
        self.excel_report = excel_report
        self.pdf_report = pdf_report
        self.ui_config = ui_config
        self.debug = debug
        self.log = LogMe(logger.setup_child_logger("patcher", __name__, debug=debug))

    @check_token
    async def process_reports(
        self,
        path: AnyStr,
        pdf: bool,
        sort: Optional[AnyStr],
        omit: bool,
        ios: bool,
        stop_event: Event,
        date_format: AnyStr = "%B %d %Y",
    ) -> None:
        """
        Asynchronously generates and saves patch reports in Excel format at the specified path,
        optionally generating PDF versions, sorting by a specified column, and omitting recent entries.

        :param path: Directory path to save the reports
        :type path: AnyStr
        :param pdf: Generate PDF versions of the reports if True.
        :type pdf: bool
        :param sort: Column name to sort the reports.
        :type sort: Optional[AnyStr]
        :param omit: Omit reports based on a condition if True.
        :type omit: bool
        :param ios: Include iOS device data if True
        :type ios: bool
        :param stop_event: Event to signal completion or abortion (used solely for animation).
        :type stop_event: threading.Event
        :param date_format: Format for dates in the header. Default is "%B %d %Y" (Month Day Year)
        :type date_format: AnyStr

        :return: None. Raises click.Abort on errors.
        """
        self.log.debug("Beginning Patcher process...")

        with exceptions.error_handling(self.log, stop_event):
            # Validate path provided is not a file
            self.log.debug("Validating path provided is not a file...")
            output_path = os.path.expanduser(path)
            if os.path.exists(output_path) and os.path.isfile(output_path):
                self.log.error(
                    f"Provided path {output_path} is a file, not a directory. Aborting...",
                )
                raise exceptions.DirectoryCreationError(path=output_path)
            else:
                self.log.debug(f"Output path '{output_path}' is valid.")

            # Ensure directories exist
            self.log.debug(
                "Attempting to create directories if they do not already exist..."
            )
            try:
                os.makedirs(output_path, exist_ok=True)
                reports_dir = os.path.join(output_path, "Patch-Reports")
                os.makedirs(reports_dir, exist_ok=True)
                self.log.info(f"Reports directory created at '{reports_dir}'.")
            except OSError as e:
                self.log.error(f"Failed to create directory: {e}")
                raise exceptions.DirectoryCreationError()

            # Async operations for patch data
            self.log.debug("Attempting to retrieve policy IDs.")
            patch_ids = await self.api_client.get_policies()

            if not patch_ids:
                self.log.error(
                    "Policy ID API call returned an empty list. Aborting...",
                )
                raise exceptions.PolicyFetchError()
            self.log.debug(f"Retrieved policy IDs for {len(patch_ids)} policies.")

            self.log.debug("Attempting to retrieve patch summaries.")
            patch_reports = await self.api_client.get_summaries(patch_ids)

            if not patch_reports:
                self.log.error("Error establishing patch summaries.")
                raise exceptions.SummaryFetchError()
            else:
                self.log.debug(
                    f"Received policy summaries for {len(patch_reports)} policies."
                )

            # (option) Sort
            if sort:
                self.log.debug(f"Detected sorting option '{sort}'")
                sort = sort.lower().replace(" ", "_")
                try:
                    patch_reports = sorted(patch_reports, key=lambda x: x[sort])
                    self.log.debug(f"Patch reports sorted by '{sort}'.")
                except KeyError:
                    self.log.error(
                        f"Invalid column name for sorting: {sort.title().replace('_', ' ')}. Aborting...",
                    )
                    raise exceptions.SortError(column=sort.title().replace("_", " "))

            # (option) Omit
            if omit:
                self.log.debug(
                    f"Detected omit flag set to {omit}. Omitting policies with patches released in past 48 hours."
                )
                cutoff = datetime.now() - timedelta(hours=48)
                original_count = len(patch_reports)
                patch_reports = [
                    report
                    for report in patch_reports
                    if datetime.strptime(report["patch_released"], "%b %d %Y") < cutoff
                ]
                omitted_count = original_count - len(patch_reports)
                self.log.debug(f"Omitted {omitted_count} policies with recent patches.")

            # (option) iOS
            if ios:
                self.log.debug(
                    f"Detected ios flag set to {ios}. Including iOS information in reports."
                )
                self.log.debug("Attempting to fetch mobile device IDs.")
                device_ids = await self.api_client.get_device_ids()
                if not device_ids:
                    self.log.error(
                        f"Received ClientError response when obtaining mobile device IDs",
                    )
                    raise exceptions.DeviceIDFetchError(
                        reason=f"Received ClientError response when obtaining mobile device IDs"
                    )

                self.log.debug(f"Obtained device IDs for {len(device_ids)} devices.")
                self.log.debug(
                    "Attempting to retrieve operating system versions for each device."
                )
                device_versions = await self.api_client.get_device_os_versions(
                    device_ids=device_ids
                )
                self.log.debug(
                    "Retrieving latest iOS version information from SOFA feed (https://sofa.macadmins.io)"
                )
                latest_versions = self.api_client.get_sofa_feed()
                if not device_versions:
                    self.log.error(
                        "Received empty response obtaining device OS versions from Jamf. Exiting...",
                    )
                    raise exceptions.DeviceOSFetchError(
                        reason="Received empty response obtaining device OS versions from Jamf."
                    )
                elif not latest_versions:
                    self.log.error("Received empty response from SOFA feed. Exiting...")
                    raise exceptions.SofaFeedError(
                        reason="Received empty response from SOFA feed. Unable to verify amount of devices on latest version."
                    )

                self.log.debug(
                    "Retrieved device version info and SOFA feed as expected. Calculating devices on latest version."
                )
                ios_data = utils.calculate_ios_on_latest(
                    device_versions=device_versions, latest_versions=latest_versions
                )
                self.log.debug("Appending iOS device information to dataframe...")
                patch_reports.extend(ios_data)
                self.log.debug(
                    "iOS information successfully appended to patch reports."
                )

            # Generate reports
            self.log.debug("Generating excel file...")
            try:
                excel_file = self.excel_report.export_to_excel(
                    patch_reports, reports_dir
                )
                self.log.debug(f"Excel file generated successfully at '{excel_file}'.")
            except ValueError as e:
                self.log.error(f"Error exporting to excel: {e}")
                raise exceptions.ExportError(file_path=excel_file)

            if pdf:
                self.log.debug(
                    f"Detected PDF flag set to {pdf}. Generating PDF file..."
                )
                try:
                    pdf_report = PDFReport(self.ui_config)
                    pdf_report.export_excel_to_pdf(excel_file, date_format)
                    self.log.debug("PDF file generated successfully.")
                except (OSError, PermissionError) as e:
                    self.log.error(
                        f"Error generating PDF file. Check file permissions: {e}"
                    )
                    raise exceptions.ExportError(file_path=excel_file)
                except Exception as e:
                    self.log.error(f"Unhandled error encountered: {e}")
                    raise exceptions.ExportError()

            stop_event.set()
            self.log.debug(
                "Patcher finished as expected. Additional logs can be found at '~/Library/Application Support/Patcher/logs'."
            )
            self.log.info(
                f"{len(patch_reports)} patch reports saved successfully to {reports_dir}."
            )
            success_msg = style(
                f"\rSuccess! Reports saved to {reports_dir}", bold=True, fg="green"
            )
            echo(success_msg)
