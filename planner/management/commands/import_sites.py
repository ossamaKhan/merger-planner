from django.core.management.base import BaseCommand, CommandError

from planner.services import importer


class Command(BaseCommand):
    help = "Import a monthly site workbook. Example: python manage.py import_sites data.xlsx --month 2026-10"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--month", help="YYYY-MM the file belongs to (not needed if the sheet has a Month column)")
        parser.add_argument("--sheet", default=None, help="Sheet name (default: Sheet1, else the first sheet)")

    def handle(self, path, month=None, sheet=None, **kw):
        m = importer.parse_month(month) if month else None
        try:
            df, used, _ = importer.read_workbook(path, sheet)
            res = importer.import_dataframe(df, month=m, source_file=path)
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Sheet '{used}': {res['rows']} rows for {', '.join(res['months'])}; "
            f"{len(res['kpi_columns'])} KPI columns; {res['no_coordinates']} without coordinates; "
            f"{res['district_mismatches']} district mismatches vs boundaries."))
