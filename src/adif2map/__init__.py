#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2026 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.

from .adif2map import load_adif, normalize_data, render_html

__all__ = ["load_adif", "normalize_data", "render_html"]

__version__ = '0.0.2'
