# SPDX-License-Identifier: GPL-2.0
"""Merge passive observer per-CPU map values; quantiles are bucket bounds."""
import math


def summarize(rows):
    bins=[0]*64
    count=total=maximum=0
    for row in rows:
        if len(row['bins'])!=64 or any(not isinstance(v,int) or v<0 for v in row['bins']):
            raise ValueError('Invalid histogram')
        if row['count']!=sum(row['bins']) or row['sum_ns']<0 or row['max_ns']<0:
            raise ValueError('Inconsistent histogram')
        count+=row['count'];total+=row['sum_ns'];maximum=max(maximum,row['max_ns'])
        bins=[a+b for a,b in zip(bins,row['bins'])]
    def quantile(q):
        if not count:return 'NOT_AVAILABLE'
        target=math.ceil(q*count);seen=0
        for i,n in enumerate(bins):
            seen+=n
            if seen>=target:return [0 if i==0 else 1<<i,min(maximum,(1<<(i+1))-1)]
        raise ValueError('Missing quantile')
    return dict(count=count,mean_ns=total/count if count else 'NOT_AVAILABLE',median_ns_bounds=quantile(.5),
                p95_ns_bounds=quantile(.95),p99_ns_bounds=quantile(.99),max_ns=maximum if count else 'NOT_AVAILABLE',bins=bins,
                scope='Observer start to sched_switch tracepoint; max is among matched observations; quantiles are intervals')
