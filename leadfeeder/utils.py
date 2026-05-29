import requests
from fivetran_connector_sdk import Logging as log
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LF_API_BASE = "https://api.leadfeeder.com/v1"

session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)
session.mount("https://", HTTPAdapter(max_retries=retry_strategy))


def _request(method, endpoint, configuration, params=None, json_body=None):
    url = f"{LF_API_BASE}/{endpoint.lstrip('/')}"
    headers = {
        "X-Api-Key": configuration.get("LEADFEEDER_API_TOKEN"),
        "Content-Type": "application/json",
        "User-Agent": "FivetranConnector/1.0",
    }
    try:
        response = session.request(
            method, url, headers=headers, params=params, json=json_body, timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        log.info(f"HTTP error calling {url}: {e}")
        raise
    except requests.exceptions.RequestException as e:
        log.info(f"Request failed calling {url}: {e}")
        raise


def _csv_join(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v is not None)
    return value


def _parse_employee_range(s):
    if not s or not isinstance(s, str):
        return None, None
    s = s.strip()
    if s.endswith("+"):
        try:
            return int(s[:-1]), None
        except ValueError:
            return None, None
    if "-" in s:
        lo, _, hi = s.partition("-")
        try:
            return int(lo), int(hi)
        except ValueError:
            return None, None
    try:
        n = int(s)
        return n, n
    except ValueError:
        return None, None


def _first_url(group):
    if not group:
        return None
    for item in group:
        if isinstance(item, dict) and item.get("url"):
            return item["url"]
    return None


def fetch_visits(params, configuration):
    account_id = configuration.get("LEADFEEDER_ACCOUNT_ID")
    body = {"start_date": params["start_date"], "end_date": params["end_date"]}
    query = {
        "account_id": account_id,
        "page[num]": params.get("page[num]") or params.get("page[number]") or 1,
        "page[size]": params.get("page[size]", 100),
        "include": "company",
    }

    visits, engagements = [], []
    leads_by_id, locations_by_id = {}, {}

    while True:
        response = _request("POST", "/web-visits", configuration, params=query, json_body=body)
        data = response.get("data") or []
        if not data:
            log.info("Response has no data, exiting loop")
            break

        log.info(f"fetched {len(data)} for page {query['page[num]']} of web-visits")

        for item in data:
            visit_id = item["id"]
            attrs = item.get("attributes") or {}
            rels = item.get("relationships") or {}
            ids = attrs.get("identifiers") or {}
            visitor = attrs.get("visitor") or {}
            location = rels.get("location") or {}
            location_attrs = location.get("attributes") or {}
            company = rels.get("company") or {}
            company_attrs = company.get("attributes") or {}
            company_id = company.get("id")
            location_id = location.get("id")

            visits.append({
                "visit_id": visit_id,
                "source": attrs.get("source"),
                "medium": attrs.get("medium"),
                "referring_url": attrs.get("referring_url"),
                "landing_page_path": attrs.get("landing_page_path"),
                "keyword": attrs.get("keyword"),
                "visit_length": attrs.get("visit_length"),
                "started_at": attrs.get("started_at"),
                "campaign": attrs.get("campaign"),
                "page_depth": attrs.get("page_depth"),
                "device_type": attrs.get("device_type"),
                "lf_client_id": ids.get("lf_client_id"),
                "ga_client_ids": _csv_join(ids.get("ga_client_ids")),
                "country_code": location_attrs.get("country_code"),
                "visitor_email": visitor.get("email"),
                "visitor_first_name": visitor.get("first_name"),
                "visitor_last_name": visitor.get("last_name"),
                "lead_id": company_id,
                "location_id": location_id,
                "account_id": str(account_id) if account_id is not None else None,
            })

            for idx, eng in enumerate(attrs.get("engagements") or []):
                page = eng.get("page") or {}
                engagements.append({
                    "visit_id": visit_id,
                    "page_number": idx + 1,
                    "event_type": eng.get("event_type"),
                    "hostname": eng.get("hostname"),
                    "page_path": page.get("path"),
                    "page_title": page.get("title"),
                    "page_url": page.get("url"),
                    "previous_page_path": eng.get("previous_page_path"),
                    "time_on_page": eng.get("time_on_page"),
                    "has_met_goals": eng.get("has_met_goals"),
                })

            if location_id and location_id not in locations_by_id:
                locations_by_id[location_id] = {
                    "location_id": location_id,
                    "country": location_attrs.get("country"),
                    "country_code": location_attrs.get("country_code"),
                    "region": location_attrs.get("region"),
                    "city": location_attrs.get("city"),
                    "postal_code": location_attrs.get("postal_code"),
                }

            if company_id and company_id not in leads_by_id:
                socials = company_attrs.get("social_media_profiles") or {}
                emp_min, emp_max = _parse_employee_range(company_attrs.get("employee_range"))
                industries_list = (company_attrs.get("industries") or {}).get("industry") or []
                industries_str = ", ".join(
                    i.get("name") for i in industries_list if i.get("name")
                )
                revenue = company_attrs.get("revenue") or {}
                web_eng = company_attrs.get("web_engagement") or {}
                leads_by_id[company_id] = {
                    "lead_id": company_id,
                    "name": company_attrs.get("name"),
                    "last_visit_date": web_eng.get("last_visit_date"),
                    "website_url": company_attrs.get("url"),
                    "linkedin_url": _first_url(socials.get("linkedin")),
                    "twitter_handle": _first_url(socials.get("twitter")),
                    "facebook_url": _first_url(socials.get("facebook")),
                    "employee_count": company_attrs.get("employee_count"),
                    "employees_range_min": emp_min,
                    "employees_range_max": emp_max,
                    "logo_url": company_attrs.get("logo_url"),
                    "business_id": company_attrs.get("vat_id"),
                    "revenue": str(revenue.get("value")) if revenue.get("value") is not None else None,
                    "industries": industries_str,
                    "location_id": location_id,
                }

        pagination = (response.get("meta") or {}).get("pagination") or {}
        page_num = pagination.get("page_num")
        page_count = pagination.get("page_count")
        if page_num is None or page_count is None or page_num >= page_count:
            log.info("No more pages, exiting loop")
            break
        query["page[num]"] = page_num + 1

    log.info(
        f"{len(visits)} visits, {len(engagements)} engagements, "
        f"{len(leads_by_id)} leads, {len(locations_by_id)} locations fetched"
    )
    return {
        "raw_leadfeeder__visits": visits,
        "raw_leadfeeder__visit_routs": engagements,
        "raw_leadfeeder__leads": list(leads_by_id.values()),
        "raw_leadfeeder__locations": list(locations_by_id.values()),
    }
