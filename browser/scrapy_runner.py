"""One subprocess per request: fresh reactor, no inherited provider credentials."""
import json
import sys
from urllib.parse import urlsplit


def main():
    import scrapy
    from scrapy.crawler import CrawlerProcess
    from protego import Protego
    from browser.scrapy_collect import extract, validate_target, MAX_BYTES
    from operations.time_integrity import utc_now, utc_text
    request = json.loads(sys.stdin.read(16384))
    url, adapter, proxy = request['url'], request['adapter'], request['proxy']
    validate_target(adapter, url)
    result = {'status': 'BLOCKED'}
    agent = 'ChiefAgentCollector/1.0'

    class Collector(scrapy.Spider):
        name = 'chief_public_collection'

        async def start(self):
            parsed = urlsplit(url)
            yield scrapy.Request(f'https://{parsed.netloc}/robots.txt', callback=self.robots,
                                 meta={'proxy': proxy, 'handle_httpstatus_all': True})

        def robots(self, response):
            if response.status == 404:
                allowed, delay = True, 1
            elif response.status == 200:
                policy = Protego.parse(response.text)
                allowed = policy.can_fetch(url, agent)
                delay = max(1, policy.crawl_delay(agent) or 1)
            else:
                return
            if not allowed or delay > 15:
                return
            # Await the publisher's crawl delay before the one data request.
            import asyncio
            async def delayed():
                await asyncio.sleep(delay)
                return [scrapy.Request(url, callback=self.parse, meta={'proxy': proxy})]
            return delayed()

        def parse(self, response):
            if response.status != 200:
                return
            result.update(status='OK', result=extract(adapter, url, response.body, utc_text(utc_now())))

    process = CrawlerProcess(settings={
        'LOG_ENABLED': False, 'TELNETCONSOLE_ENABLED': False,
        'USER_AGENT': agent, 'COOKIES_ENABLED': False, 'ROBOTSTXT_OBEY': False,
        # robots is fetched explicitly above: fail closed on errors, honor crawl delay.
        'REDIRECT_ENABLED': False, 'METAREFRESH_ENABLED': False, 'RETRY_ENABLED': False,
        'HTTPPROXY_ENABLED': True, 'DOWNLOAD_TIMEOUT': 15, 'DOWNLOAD_MAXSIZE': MAX_BYTES,
        'DOWNLOAD_WARNSIZE': MAX_BYTES, 'CONCURRENT_REQUESTS': 1,
        'DOWNLOAD_DELAY': 1, 'RANDOMIZE_DOWNLOAD_DELAY': False,
        'DOWNLOADER_CLIENTCONTEXTFACTORY': 'scrapy.core.downloader.contextfactory.BrowserLikeContextFactory',
        'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor',
        'DOWNLOAD_HANDLERS': {'file': None, 'data': None, 'ftp': None},
    })
    process.crawl(Collector)
    process.start()
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"FAILED"}')
        sys.exit(1)
