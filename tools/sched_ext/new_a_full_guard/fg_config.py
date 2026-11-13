# SPDX-License-Identifier: GPL-2.0
"""Fixed 8T instrumentation comparison, never a new placement policy."""
LABELS=dict(record_type='EXPLORATORY_ONLY',strict_acceptance='FAILED',safety_verdict='FAIL',suite_acceptance='NOT_EVALUATED',formal_benchmark=False)
ORDERS=(('FULL','GUARD'),('GUARD','FULL'))*3
PLAN=[dict(round=r,position=p,policy=mode,visit_id=f'r{r:02d}-{mode}',relative_dir=f'visits/round-{r:02d}/{mode}')
      for r,order in enumerate(ORDERS,1) for p,mode in enumerate(order,1)]
SERVER_CPUS={0,2,4,6,8,9,10,11,12,13,14}
HOUSE_CPUS={15}
NA='NOT_AVAILABLE'
METRICS=('prefill_tps','prefill_ms','http_request_ms','internal_inference_ms','decode_tps','decode_ms',
         'prefill_internal_share_pct','decode_internal_share_pct','prefill_http_share_pct','decode_http_share_pct',
         'proc_window_ms','cpu_time_s','context_switches','migrations','migration_per_cpu_s','context_switches_per_cpu_s',
         'p_mean_busy_pct','e_mean_busy_pct','system_busy_pct')

REQUEST_SHA='7b68e4809a1f9bca18c2bc29b87c62c637e19e2ccd3785745cc717c046999e1d'
