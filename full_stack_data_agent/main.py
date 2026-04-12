from __future__ import annotations

import json

from full_stack_data_agent.bootstrap import bootstrap


def main() -> None:
    service = bootstrap()
    print(json.dumps(service.provider_status().to_dict(), indent=2))


if __name__ == "__main__":
    main()
