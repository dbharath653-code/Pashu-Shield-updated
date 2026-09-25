#!/usr/bin/env python3
"""Render pbx/asterisk/*.template files from a KEY=VALUE env file.

Supports {{VAR}} substitution and {{#IF VAR}}...{{/IF}} conditionals
(truthy = non-empty and not 0/false/no/off). Stdlib only.
Usage: render.py <envfile> <template> <output>
"""
import os
import re
import sys


def load_env(path):
    env = {}
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            env[k] = v
    return env


def truthy(v):
    return bool(v) and v.lower() not in ("0", "false", "no", "off")


def render(text, env):
    def _if(m):
        var, body = m.group(1), m.group(2)
        return body if truthy(env.get(var, "")) else ""
    text = re.sub(r"\{\{#IF\s+([A-Za-z0-9_]+)\}\}(.*?)\{\{/IF\}\}", _if, text,
                  flags=re.S)

    def _var(m):
        return env.get(m.group(1), "")
    text = re.sub(r"\{\{([A-Za-z0-9_]+)\}\}", _var, text)
    return text


def main(argv):
    if len(argv) != 4:
        print("usage: render.py <envfile> <template> <output>", file=sys.stderr)
        return 2
    envfile, template, output = argv[1], argv[2], argv[3]
    env = dict(os.environ)
    env.update(load_env(envfile))
    with open(template) as fh:
        text = fh.read()
    rendered = render(text, env)
    with open(output, "w") as fh:
        fh.write(rendered)
    leftovers = sorted(set(re.findall(r"\{\{[#/]?[A-Za-z0-9_ ]+\}\}", rendered)))
    if leftovers:
        print("render: unexpanded tags in %s: %s" % (output, leftovers), file=sys.stderr)
        return 1
    print("render: %s -> %s" % (template, output))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
