"""
Benchmark Bigchain throughput of CREATE transactions.

The throughput of BigchainDB cannot be measured just by posting transactions
via the web interface, because the process whereby they are validated is
asynchronous.

For this reason, this benchmark also monitors the size of the backlog, so that
transactions do not become stale, which can result in thrashing.

The benchmark runs for a fixed period of time and makes metrics available via
a graphite instance.

It should work in any environment as long as Docker Compose is available.

To start:

    $ python3 scripts/bench_create.py

To start using a separate namespace for docker-compose:

    $ COMPOSE_PROJECT_NAME=somename python3 scripts/bench_create.py

Happy benchmarking!
"""


import sys
import math
import time
import requests
import subprocess
import multiprocessing


def main():
    cmd('docker-compose up -d mdb')
    cmd('docker-compose up -d bdb')
    cmd('docker-compose up -d graphite')

    out = cmd('docker-compose port graphite 80', capture=True)
    graphite_web = 'http://localhost:%s/' % out.strip().split(':')[1]
    print('Graphite web interface at: ' + graphite_web)

    start = time.time()

    cmd('docker-compose exec bdb python %s load' % sys.argv[0])

    mins = math.ceil((time.time() - start) / 60) + 1

    graph_url = graphite_web + 'render/?width=900&height=600&_salt=1495462891.335&target=stats_counts.pipelines.block.throughput&target=stats_counts.pipelines.vote.throughput&target=stats_counts.web.tx.post&from=-%sminutes' % mins  # noqa

    print(graph_url)


def load():
    from bigchaindb.core import Bigchain
    from bigchaindb.common.crypto import generate_key_pair
    from bigchaindb.common.transaction import Transaction

    def transactions():
        priv, pub = generate_key_pair()
        tx = Transaction.create([pub], [([pub], 1)])
        while True:
            i = yield tx.to_dict()
            tx.asset = {'data': {'n': i}}
            tx.sign([priv])

    def wait_for_up():
        print('Waiting for server to start... ', end='')
        while True:
            try:
                requests.get('http://localhost:9984/')
                break
            except requests.ConnectionError:
                time.sleep(0.1)
        print('Ok')

    def post_txs():
        txs = transactions()
        txs.send(None)
        try:
            with requests.Session() as session:
                while True:
                    i = tx_queue.get()
                    if i is None:
                        break
                    tx = txs.send(i)
                    res = session.post('http://localhost:9984/api/v1/transactions/', json=tx)
                    assert res.status_code == 202
        except KeyboardInterrupt:
            pass

    wait_for_up()
    num_clients = 30
    test_time = 60
    tx_queue = multiprocessing.Queue(maxsize=num_clients)
    txn = 0
    b = Bigchain()

    start_time = time.time()

    for i in range(num_clients):
        multiprocessing.Process(target=post_txs).start()

    print('Sending transactions')
    while time.time() - start_time < test_time:
        for i in range(500):
            tx_queue.put(txn)
            txn += 1
        print(txn)
        while True:
            count = b.connection.db.backlog.count()
            if count > 10000:
                time.sleep(0.2)
            else:
                break

    for i in range(num_clients):
        tx_queue.put(None)

    print('Waiting to clear backlog')
    while True:
        bl = b.connection.db.backlog.count()
        if bl == 0:
            break
        print(bl)
        time.sleep(1)

    print('Waiting for all votes to come in')
    while True:
        blocks = b.connection.db.bigchain.count()
        votes = b.connection.db.votes.count()
        if blocks == votes + 1:
            break
        print('%s blocks, %s votes' % (blocks, votes))
        time.sleep(3)

    print('Finished')


def cmd(command, capture=False):
    stdout = subprocess.PIPE if capture else None
    args = ['bash', '-c', command]
    proc = subprocess.Popen(args, stdout=stdout)
    assert not proc.wait()
    return capture and proc.stdout.read().decode()


if sys.argv[1:] == ['load']:
    load()
else:
    main()
