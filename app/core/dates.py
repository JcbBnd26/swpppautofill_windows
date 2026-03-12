from datetime import datetime, timedelta
from dateutil.parser import isoparse

def weekly_dates(start_date_str: str, end_date_str: str):
    start = isoparse(start_date_str).date()
    end = isoparse(end_date_str).date()
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    cur = start
    while cur <= end:
        yield datetime.combine(cur, datetime.min.time())
        cur = cur + timedelta(weeks=1)
