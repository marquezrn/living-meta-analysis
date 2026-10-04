import httpx
import pytest

from livingmeta.acquisition import download_pdf, validate_download_url


@pytest.mark.parametrize("url", ["http://arxiv.org/a.pdf", "https://127.0.0.1/a.pdf", "https://arxiv.org.evil.test/a.pdf",
                                "https://user:password@arxiv.org/a.pdf", "https://arxiv.org:8000/a.pdf"])
def test_access_downloads_reject_unapproved_destinations(url):
    with pytest.raises(ValueError):
        validate_download_url(url)


async def test_redirects_cannot_escape_trusted_hosts():
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://127.0.0.1/internal"}))
    with pytest.raises(ValueError):
        await download_pdf("https://arxiv.org/source.pdf", 1000, transport=transport)


async def test_bounded_pdf_access_never_accepts_a_paywall_page():
    for content, limit in [(b"<html>sign in</html>", 1000), (b"%PDF-synthetic", 5)]:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
        with pytest.raises(ValueError):
            await download_pdf("https://arxiv.org/source.pdf", limit, transport=transport)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"%PDF-synthetic"))
    content, url = await download_pdf("https://arxiv.org/source.pdf", 1000, transport=transport)
    assert content.startswith(b"%PDF-") and url == "https://arxiv.org/source.pdf"
