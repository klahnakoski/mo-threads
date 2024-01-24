from mo_threads import wait_for_shutdown_signal

print("hello")
wait_for_shutdown_signal(wait_forever=False)
