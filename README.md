# command-helper
Command helper is a functional easy to use command stacker. Install with ''pip install .'', or ''pip install -e .''. Use case:
I want an automative java to jar maker...
ah! lets use cmdh!
usage:

cmdh -init
# this will set configure.json, it will ask for a setting and the commands.
cmdh -build
# this will use configure.json in the directory and do whatever it reads. 
Security Warning: RUN UNKNOWN CONFIGS AT YOUR OWN RISK
# For a more detailed usage:
cmdh — a tiny, dependency-free build/command runner ("mini gradle").

Works unmodified on Windows, Linux, and Android/Termux because it just
hands each configured command to the system shell (cmd.exe on Windows,
sh on everything else) and reports back what happened.

Config format (config.json), keys are the *execution order*, not IDs:

    {
        "1": "javac -d build src/Main.java",
        "2": "aapt2 compile -o build res",
        "3": "not_a_real_command foo"
    }

Steps run in ascending numeric order of the keys (so "2" always runs
before "10" — plain string sort would get that wrong, which is why we
sort by int(key) instead).

Each value can also be an object instead of a plain string, if you want
a friendly label or a per-step override:

    "1": { "cmd": "javac -d build src/Main.java", "name": "Compile Java" }

Usage:
    cmdh -init                     interactively create ./config.json
    cmdh -build                    run the steps in ./config.json
    cmdh -build -c other.json      run a specific config file
    cmdh -build --continue-on-error   don't stop after a failing step
    cmdh -build --dry-run          print the plan, run nothing
    cmdh -build -q                 quiet console (log file still full)
