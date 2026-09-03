"""Ask a host whether we may fetch it, and print the answer (ADR-012).

    python -m scripts.check_host_permission [HOST ...]

Adding a host to `ALLOWED_HOSTS` is a judgement about somebody else's terms of
service, and no test in this repository can make it for you. This gives the
person making it the facts: what the host's robots.txt actually says, about
*our* user-agent token, for the exact paths the rule would request.

Run it before setting `SCRAPER_ENABLED_HOSTS`, and again occasionally -- a
robots.txt is a live document, and a host that said yes last year is entitled to
change its mind.

Read-only. It fetches one file per host and writes nothing, so it is safe to run
against production configuration.
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import quote

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNREACHABLE = 2


async def check(host: str) -> int:
    from app.adapters.offers.scraper import robots
    from app.adapters.offers.scraper.hosts import ALLOWED_HOSTS, UA_TOKEN, user_agent
    from app.core.config import get_settings
    from app.core.database import worker_async_session

    rule = ALLOWED_HOSTS.get(host)
    if rule is None:
        print(f"{host}: no HostRule. Configuration cannot enable a host the code cannot read.")
        return EXIT_REFUSED

    settings = get_settings()
    print(f"host:       {host}")
    print(f"user-agent: {user_agent(settings)}")
    print(f"token:      {UA_TOKEN}")
    print()

    # A real session: the policy is cached in Postgres, and running this warms
    # that cache with the same row the adapter would use.
    async with worker_async_session() as session:
        policy = await robots.policy_for(session, host, token=UA_TOKEN)
        await session.commit()

    if policy.fetch_failed:
        print("robots.txt: UNREACHABLE")
        print()
        print("  Treated as a full disallow. ADR-012 fails closed: unreachable is not")
        print("  permission, and the adapter will refuse every path until this clears.")
        return EXIT_UNREACHABLE

    if policy.parser is None:
        print("robots.txt: 404 -- no restrictions (RFC 9309)")
    else:
        print("robots.txt: fetched")
        if policy.crawl_delay_seconds is not None:
            print(f"crawl-delay: {policy.crawl_delay_seconds}s")
            if policy.crawl_delay_seconds > rule.min_interval_seconds:
                print(
                    f"  NOTE: the host asks for {policy.crawl_delay_seconds}s but the rule "
                    f"sets {rule.min_interval_seconds}s. Raise `min_interval_seconds`."
                )

    print()
    refused = 0
    # The real search URL as well as the prefixes: a prefix can be allowed while
    # the query path underneath it is not.
    probes = [*rule.allowed_path_prefixes, rule.search_url.format(query=quote("milk"))]
    for probe in probes:
        url = probe if probe.startswith("http") else f"https://{host}{probe}"
        allowed = policy.allows(url, UA_TOKEN)
        print(f"  {'ALLOW ' if allowed else 'DENY  '} {url}")
        refused += 0 if allowed else 1

    print()
    if refused:
        print(f"{refused} of {len(probes)} paths refused. Do not enable this host.")
        return EXIT_REFUSED

    print("Every configured path is permitted for our token.")
    print()
    print("That is necessary and not sufficient: robots.txt is not a licence, and it")
    print("says nothing about the host's terms of service or how its data may be")
    print("reused. Read those before adding the host to SCRAPER_ENABLED_HOSTS.")
    return EXIT_OK


def main() -> int:
    from app.adapters.offers.scraper.hosts import ALLOWED_HOSTS

    hosts = sys.argv[1:] or sorted(ALLOWED_HOSTS)
    if not hosts:
        print("No hosts configured in ALLOWED_HOSTS, and none given.")
        print("A fresh deployment is incapable of fetching anything, by design.")
        return EXIT_OK

    worst = EXIT_OK
    for index, host in enumerate(hosts):
        if index:
            print("\n" + "-" * 72 + "\n")
        worst = max(worst, asyncio.run(check(host)))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
