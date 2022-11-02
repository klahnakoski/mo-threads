from mo_threads import stop_main_thread, wait_for_shutdown_signal

try:
    print("hello")
    wait_for_shutdown_signal(wait_forever=False)
finally:
    stop_main_thread()
