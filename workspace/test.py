"""有意保留并发不安全写法的示例"""

from __future__ import annotations

import threading


def main() -> None:
    # 全局计数器
    count = 0
    threads: list[threading.Thread] = []

    # 启动 1000 个线程去并发累加
    for _ in range(1000):
        def worker() -> None:
            nonlocal count
            # TODO: 这里有没有并发安全问题？
            count += 1

        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"最终的 Count 是: {count}")


if __name__ == "__main__":
    main()
