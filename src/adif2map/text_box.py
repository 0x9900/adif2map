# vim:fenc=utf-8
#
# Copyright © 2026 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.

from branca.element import MacroElement, Template


class TextBox(MacroElement):
  _template = Template("""
    {% macro html(this, kwargs) %}
    <!doctype html>
    <html lang="en">
      <body>
        <div id="textbox" class="textbox">
          <div class="textbox-title">{{ this.title }}</div>
          <div class="textbox-content">
            <a href="https://qsomap.bsdworld.org/">https://qsomap.bsdworld.org/</a>
          </div>
        </div>
      </body>
    </html>

    <style type='text/css'>
      .textbox {
        background: rgba(255, 255, 255, .6);
        border-radius: 6px;
        border: 1.5px solid salmon;
        padding: 5px;
        position: absolute;
        bottom: 15px;
        left: 15px;
        z-index: 9999;
      }
      .textbox .textbox-title {
        color: salmon;
        font-size: 14px;
        font-weight: bold;
        text-align: center;
      }
      .textbox .textbox-content {
        font-size: 12px;
        text-align: center;
      }
    </style>
    {% endmacro %}
  """)

  def __init__(self, title):
    super().__init__()
    self._name = "TextBox"
    self.title = title


def add_infobox(chart, title):
  chart.get_root().add_child(TextBox(title))
