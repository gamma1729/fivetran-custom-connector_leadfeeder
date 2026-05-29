from fivetran_connector_sdk import Connector, Operations as op, Logging as log
from datetime import datetime, timedelta
from utils import fetch_visits
from state import get_state, update_state
from schema import get_schema

def schema(configuration):
    return get_schema(configuration)



def update_visits(configuration, state):
    yield from sync_records(
        fetch_function= fetch_visits,
        state= state,
        table_to_update= 'raw_leadfeeder__visits',
        configuration= configuration
    )

def sync_records(configuration, fetch_function, state, table_to_update):
    state_key = f'last_{table_to_update}_sync'
    today = datetime.today().date()
    start_date_override = configuration.get('start_date_override')
    log.info(f'Getting state with {state_key}')
    start_date_from_state = get_state(state, state_key)
    log.info(f'last sync returned by state {start_date_from_state}')
    
    if not start_date_override:
        start_date = datetime.strptime(start_date_from_state, "%Y-%m-%d").date()
    else:
        start_date = datetime.strptime(start_date_override, "%Y-%m-%d").date()
   

    while start_date < today:
        log.info(f'Now processing records for {start_date}')

        params = {
            'start_date': start_date.strftime("%Y-%m-%d"),
            'end_date': start_date.strftime("%Y-%m-%d"),
            'page[num]': 1,
            'page[size]': int(configuration.get("page_size", 100))
        }
        records_to_upsert = fetch_function(params, configuration)
        if not records_to_upsert:
            log.info('No records to update')
        for table_name, records in records_to_upsert.items():
                log.info(f'Prepping {len(records)} to upsert in {table_name}')
                for record in records:
                    yield op.upsert(
                    table= table_name,
                    data= record
                )

        log.info(f'updating checkpoint {state_key} with {start_date.strftime("%Y-%m-%d")}')
        update_state(state, state_key, start_date)
        yield op.checkpoint(state)
        start_date += timedelta(days=1)

def update(configuration, state):
    yield from update_visits(configuration, state)

connector = Connector(schema=schema, update=update)

if __name__ == "__main__":
    import json
    with open("configuration.json", "r") as f:
        configuration = json.load(f)
    connector.debug(configuration=configuration)
