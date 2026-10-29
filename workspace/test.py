"""修复并发安全问题的示例"""

from __future__ import annotations

import threading


def main() -> None:
    # 全局计数器
    count = 0
    lock = threading.Lock()  # 使用锁保护共享资源
    threads: list[threading.Thread] = []

    # 启动 1000 个线程去并发累加
    for _ in range(1000):
        def worker() -> None:
            nonlocal count
            with lock:  # 使用上下文管理器确保锁的正确释放
                count += 1

        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print(f"最终的 Count 是: {count}")


if __name__ == "__main__":
    main()
