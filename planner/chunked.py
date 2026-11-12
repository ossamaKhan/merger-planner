"""Large uploads in pieces (Vercel's 4.5 MB request limit).

The browser (static/planner/chunked.js) sends files over ~3 MB to /api/chunk/ in 3 MB pieces, then submits
the original form with `chunk_token` + `chunk_name` instead of the file. This middleware rebuilds the file
and puts it back in request.FILES["file"], so every upload view works unchanged."""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import JsonResponse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict


class ChunkedUploadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.content_type == "multipart/form-data" and not request.path.startswith("/api/chunk"):
            token = request.POST.get("chunk_token")
            if token:
                from .models import UploadChunk
                parts = UploadChunk.objects.filter(token=token).order_by("idx")
                raw = b"".join(bytes(p.data) for p in parts)
                if raw:
                    files = request.FILES.copy() if request.FILES else MultiValueDict()
                    files["file"] = SimpleUploadedFile(request.POST.get("chunk_name") or "upload.bin", raw)
                    request._files = files
                parts.delete()
        return self.get_response(request)


def api_chunk(request):
    from .models import UploadChunk
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    if getattr(request, "user", None) is not None:
        from django.conf import settings
        if settings.PLANNER_REQUIRE_LOGIN and not request.user.is_authenticated:
            return JsonResponse({"error": "login required"}, status=403)
    token = request.headers.get("X-Chunk-Token", "")[:40]
    idx = int(request.headers.get("X-Chunk-Index", "0"))
    if not token:
        return JsonResponse({"error": "missing token"}, status=400)
    UploadChunk.objects.update_or_create(token=token, idx=idx, defaults={"data": request.body})
    if idx == 0:  # tidy up pieces of abandoned uploads
        UploadChunk.objects.filter(created_at__lt=timezone.now() - datetime.timedelta(hours=6)).delete()
    return JsonResponse({"ok": True})
