from django.core.management.base import BaseCommand

from planner.services import geo, importer


class Command(BaseCommand):
    help = "Re-assign boundary district / tehsil to every site (run after replacing the GeoJSON files in planner/data/)."

    def handle(self, **kw):
        geo._load.cache_clear()
        n = importer.retag_all_sites()
        self.stdout.write(self.style.SUCCESS(f"Re-tagged {n} site rows."))
