from unittest import SkipTest

from mo_future import get_function_name
from mo_logs import Log


def add_error_reporting(suite):
    def add_hanlder(function):
        test_name = get_function_name(function)
        def error_hanlder(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except SkipTest as cause:
                raise cause
            except Exception as cause:
                Log.warning(f"{test_name} failed", cause)
                raise cause

        return error_hanlder

    if not hasattr(suite, "FuzzyTestCase.__modified__"):
        setattr(suite, "FuzzyTestCase.__modified__", True)
        # find all methods, and wrap in exceptin handler
        for name, func in vars(suite).items():
            if name.startswith("test"):
                h = add_hanlder(func)
                h.__name__ = get_function_name(func)
                setattr(suite, name, h)
    return suite
