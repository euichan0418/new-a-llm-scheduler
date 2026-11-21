# SPDX-License-Identifier: GPL-2.0
"""Public fixed study plan only; not runtime or performance admission."""
CPUS=[0,2,4,6,8,9,10,11]

def plans():
    functional = [dict(id=cell+'-'+mode, cell=cell, role=mode, compute=4, batch=8, http=2,
        cpus=CPUS, warmups=2, measured_requests=1, performance_sample=False)
        for cell in ['C48', 'E48', 'L48'] for mode in ['audit', 'clean']]
    performance = [dict(id=f'r{block:02d}-{cell}', block=block, position=position, cell=cell,
        compute=4, batch=8, http=2, cpus=CPUS, warmups=2, measured_requests=1)
        for block, order in enumerate([['C48','E48','L48'], ['L48','E48','C48']], 1)
        for position, cell in enumerate(order, 1)]
    return dict(FUNCTIONAL=functional, PERFORMANCE=performance)
