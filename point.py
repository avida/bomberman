#! /usr/bin/env python3

###
# #%L
# Codenjoy - it's a dojo-like platform from developers to developers.
# %%
# Copyright (C) 2018 Codenjoy
# %%
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/gpl-3.0.html>.
# #L%
###


class Point:
    """ Describes a point on board."""
    def __init__(self, x=0, y=0):
        self._x = int(x)
        self._y = int(y)

    def get(self):
        return self._x, self._y
    
    def __key(self):
        return self._x, self._y

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return self.to_string()

    def __eq__(self, other_point):
        return self.__key() == other_point.__key()

    def __sub__(self, other_point):
        return self._x - other_point._x, self._y - other_point._y

    def __add__(self, other_point):
        if not other_point:
            return self
        return Point(self._x + other_point._x, self._y + other_point._y)

    def distance(self, other):
        dx, dy = self - other
        return abs(dx) + abs(dy)

    def add_tupl(self, tpl):
        return Point(self._x + tpl[0], self._y + tpl[1])

    def __hash__(self):
        return hash(self.__key())

    def get_x(self):
        return self._x

    def get_y(self):
        return self._y

    def is_bad(self, board_size):
        return (self._x >= board_size or self._x < 0 or
                self._y >= board_size or self._y < 0)

    def to_string(self):
        return "[{},{}]".format(self._x, self._y)


if __name__ == '__main__':
    raise RuntimeError("This module is not expected to be ran from CLI")
