from __future__ import annotations

from app import db_cursor, execute_sync, get_sync_settings, should_run_monthly


def main() -> int:
    with db_cursor() as (_, cur):
        settings = get_sync_settings(cur)
    if not should_run_monthly(settings):
        print('[mine-city-reiki] no scheduled sync due')
        return 0
    scope = settings.get('source_scope') or 'all'
    print(f'[mine-city-reiki] scheduled sync started: {scope}')
    execute_sync('scheduled', scope)
    print('[mine-city-reiki] scheduled sync finished')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
