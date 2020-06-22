#!/usr/bin/env python3

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

import logging
from time import time
from random import choice
from board import Board
from element import Element
from direction import Direction, _DIRECTIONS
from point import Point
import random
from itertools import product
from collections import defaultdict
from dataclasses import dataclass
import traceback

from pathfinding.core.diagonal_movement import DiagonalMovement
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder
from element import _ELEMENTS
from enum import Enum

BLAST_RANGE = 3

class Mode(Enum):
    KILL = 1
    ROAMING = 2
    PANIC = 3
    PERK_HUNT = 4

class NextMoves:
    def __init__(self, *acts):
        self._moves = acts
        dr = list(filter(lambda x: x not in ["ACT", "NONE"], self._moves))
        self.direction = dr[0] if dr else None

    def __str__(self):
        return ",".join(self._moves)
        

DESTROY_MODES = [Mode.KILL, Mode.ROAMING]
NOT_PASSIBLE = {
    _ELEMENTS["WALL"],
    _ELEMENTS["DESTROY_WALL"],
    _ELEMENTS["MEAT_CHOPPER"],
    _ELEMENTS["OTHER_BOMBERMAN"],
    _ELEMENTS["BOMB_TIMER_1"],
    _ELEMENTS["BOMB_TIMER_2"],
    _ELEMENTS["BOMB_TIMER_3"],
    _ELEMENTS["BOMB_TIMER_4"],
    _ELEMENTS["BOMB_TIMER_5"],
    _ELEMENTS["DEAD_MEAT_CHOPPER"],
    }


def setup_logging():
    logger = logging.getLogger("bot")
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s:  %(message)s')

    hndl = logging.StreamHandler()
    hndl.setFormatter(formatter)
    hndl.setLevel(logging.INFO)
    fh = logging.FileHandler("bot.log")
    fh.setFormatter(formatter)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(hndl)
    logger.addHandler(fh)
    return logger
    
logger = setup_logging()
    
@dataclass
class ModeInfo():
    mode: Mode
    target: Point


class DirectionSolver:
    """ This class should contain the movement generation algorithm."""

    def __init__(self):
        self._direction = None
        self._board = None
        self._last = None
        self._victim = None
        self._count = 0
        self._me = None
        self._bomb_placed = -1000
        self._prev_bombermans = set()
        self._target_point = None
        self._mode = None
        self._prev_players_num = 0
        self._panics = 0
        self._choppers = set()
    
    @staticmethod
    def get_direction(pnt_from, pnt_to):
        dir_vec = {
            Point(0,1): "DOWN",
            Point(0,-1): "UP",
            Point(-1,0): "LEFT",
            Point(1,0): "RIGHT",
        }
        vec = Point(pnt_to.get_x() - pnt_from.get_x(), pnt_to.get_y() - pnt_from.get_y())
        return dir_vec.get(vec)

    def direction_to_point(self, dr):
        dir_vec = {
            "DOWN":Point(0,1),
            "UP": Point(0,-1),
            "LEFT": Point(-1,0),
            "RIGHT": Point(1,0),
        }
        return self._me + dir_vec.get(dr)

    @staticmethod
    def _replace_walls(s):
        return 0 if s in NOT_PASSIBLE else 100

    def get_quadrant(self, pnt: Point):
        sz = self._board._size // 2
        x, y = pnt.get()
        qd = "L" if x <= sz else "R"
        qd += "B" if y > sz else "T"
        return qd

    def is_place_safe(self, place = None):
        if not place:
            place = self._me
        return place not in self._board.get_barriers() and \
               place not in self._future_blasts and \
               place not in self._choppers and \
               place not in self._mad_choppers

    def get_path(self, to_pnt: Point, grid = None):
        if not grid:
            grid = self._make_grid()
        pnt = grid.node(self._me.get_x(), self._me.get_y())
        pnt.walkable = True
        target_node = grid.node(to_pnt.get_x(), to_pnt.get_y())
        target_node.walkable = True
        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)
        path, runs = finder.find_path(pnt, target_node, grid)
        self._grid = grid
        return path

    def _make_grid(self):
        return Grid(matrix=self._matrix)

    def get_other_player_path(self, afk_players):
        grid = Grid(matrix=self._matrix)
        self._grid = grid
        pnt = grid.node(self._me.get_x(), self._me.get_y())
        pnt.walkable = True

        if not afk_players:
            return None

        for bomber in afk_players:
            
            if self._victim and self._victim  == bomber:
                continue
            path = self.get_path(bomber, grid)
            if path:
                #logger.debug(grid.grid_str(path=path,start=pnt))
                return path
            grid.cleanup()
        return None
        
    def get_safe_place(self, radius = 5):
        places = []
        for dx, dy in product(range(-radius, radius), range(-radius, radius)):
            place = Point(self._me.get_x() + dx, self._me.get_y()+dy)
            if not place.is_bad(self._board._size) and \
                place not in self._board.get_barriers() and \
                place not in self._future_blasts:
                places.append(place)
        places = sorted(places, key = lambda x: x.distance(self._me), reverse = True)
        return places

    def get_good_place(self, places):
        grid = Grid(matrix=self._matrix)
        self._grid = grid
        path_list = []
        for place in places:
            path = self.get_path(place, grid)
            grid.cleanup()
            if path:
                path_list.append(path)
        return path_list

    def get_potential_yield(self, current_point):
        pnts = 0
        points = {
            _ELEMENTS["DESTROY_WALL"]: 1,
            _ELEMENTS["OTHER_BOMBERMAN"]: 20,
            _ELEMENTS["MEAT_CHOPPER"]: 10,
        }

        break_el = [Element("WALL"), Element("DESTROY_WALL")]

        def get_points(pnt: Point):
            if pnt in self._mad_choppers:
                return True, 10
            el = self._board.get_at(pnt.get_x(), pnt.get_y())
            return el in break_el, points.get(el.get_char(), 0)

        ranges = [range(1, BLAST_RANGE), range(-1, -BLAST_RANGE , -1)]
        for rg in ranges:
            for dx in rg:
                pnt = Point(current_point.get_x()+dx, current_point.get_y())
                brk, _pnts = get_points(pnt)
                pnts += _pnts
                logger.debug(f" dx {dx}, {brk}, {_pnts}")
                if brk:
                    break
            for dy in rg:
                pnt = Point(current_point.get_x(), current_point.get_y()+dy)
                _pnts = get_points(pnt)
                brk, _pnts = get_points(pnt)
                pnts += _pnts
                logger.debug(f" dy {dy} {brk}, {_pnts}")
                if brk:
                    break

        return pnts
    
    def get_walls_density(self):
        walls_dens = defaultdict(list)
        for wall in self._destroy_walls:
            dr = self.get_quadrant(wall)
            walls_dens[dr].append(wall)
        return sorted(walls_dens.items(), key = lambda x: len(x[1]), reverse=True)

    def get_random_point(self, quadrant):
        dx, dy = quadrant
        sz = self._board._size // 2
        x_range = (1, sz ) if dx == "L" else (sz, self._board._size-1)
        y_range = (1, sz ) if dy == "T" else (sz, self._board._size-1)
        return Point(random.randrange(*x_range), random.randrange(*y_range))

    def get_roaming_point(self):
        walls_dens = self.get_walls_density()
        points = set()
        qd  = walls_dens[0][0]
        points = walls_dens[0][1]
        logger.info(qd)
        logger.info(points)
        points = sorted(points, key = lambda x: x.distance(self._me), reverse=True)
        logger.info(points)
        logger.info(list(map(lambda x: x.distance(self._me),points)))
        return points
    
    def get_potential_chopper_moves(self):
        choppers = self._choppers.union(self._mad_choppers)
        ch_moves = set()
        for chopper in choppers:
            for d_tpl in filter(lambda x: x[0] != x[1] and (x[0]== 0 or  x[1] == 0), 
                                product([0, 1, -1], [0, 1, -1])):
                pnt = chopper.add_tupl(d_tpl)
                if not pnt.is_bad(self._board._size) and \
                    self._board.get_at(*pnt.get()).get_char() not in [_ELEMENTS["DESTROY_WALL"], _ELEMENTS["WALL"]]:
                    ch_moves.add(pnt)
        return ch_moves

    def get_near_perks(self):
        PERK_RADIUS = 6
        logger.debug(f"Perks: {self._perks}")
        perks = list(filter(lambda x: self._me.distance(x) <= PERK_RADIUS, self._perks))
        return perks

    def get_near_perk_path(self):
        near_perks = self.get_near_perks()
        grid = self._make_grid()
        for perk in near_perks:
            path = self.get_path(perk, grid)
            if path:
                return path
            grid.cleanup()

    def _make_matrix(self):
        matrix = self._board._line_by_line().split('\n')
        for i,val in enumerate(matrix):
            matrix[i] = list(map(self._replace_walls, val))
        perks = self._board.get_perks()
        for perk in perks:
            matrix[perk.get_y()][perk.get_x()] = 1

        chopper_move = self.get_potential_chopper_moves()
        self._next_choppers_moves = chopper_move
        for ch_move in chopper_move:
            matrix[ch_move.get_y()][ch_move.get_x()]+= 2000

        future_blasts = self._board.get_future_blasts()
        for fb in future_blasts:
            matrix[fb.get_y()][fb.get_x()] *= 5

        future_blasts = self._board.get_future_blasts(True)
        self._future_blasts = future_blasts
        for fb in future_blasts:
            matrix[fb.get_y()][fb.get_x()] = 0
        return matrix


    def get_deco(f):
        def wrapper(self, board_string):
            import time
            start_time = time.time()
            self._count +=1
            logger.info(f"{10*'-'} tick: {self._count}")
            board = Board(board_string)
            self._board = board
            dead_choppers = set(board.get_dead_choppers())
            self._mad_choppers = dead_choppers - self._choppers
            logger.debug(f"aaah Mad choppers: {self._mad_choppers}")
            self._choppers = set(board.get_meat_choppers())
            self._perks = board.get_perks()
            self._other_players = board.get_other_bombermans()
            self._me = board.get_bomberman()
            self._destroy_walls = board.get_destroy_walls()
            self._matrix = self._make_matrix()
            self._is_bomb_placed = (self._count - self._bomb_placed) <= 4
            logger.info(self._board.to_string())
            res = "NONE"
            try:
                res = f(self, board_string)
            except Exception as e:
                exc_info = traceback.format_exc()
                logger.error(f"Unexpected exception occured: {exc_info}")
            self._prev_bombermans = self._other_players
            self._prev_players_num = len(self._other_players)
            self._prev_perks = self._perks
            if "ACT" in res:
                self._bomb_placed = self._count
            logger.info(f"send command: --->{res}<--- decision time: {time.time() - start_time} seconds")
            return res
        return wrapper

    def get_kill_path(self):
        afk_bots = self._prev_bombermans.intersection(self._other_players)
        logger.info(f"afk bots:{afk_bots}") 
        path = self.get_other_player_path(afk_bots)
        logger.info(f"kill path {path}")
        return path

    def calculate_next_path(self):
        if self._mode.mode in DESTROY_MODES:
            target_path = self.get_path(self._mode.target)
            return target_path
        return None

    def pick_mode(self):
        logger.info("Picking new mode")
        perks_path = self.get_near_perk_path()
        if perks_path:
            logger.info("PERK HUNT!!")
            target_pnt = Point(*perks_path[-1])
            return ModeInfo(Mode.PERK_HUNT,target_pnt), perks_path

        kill_path = self.get_kill_path()
        self._victim = None
        if kill_path:
            logger.info("KILL!!")
            target_pnt = Point(*kill_path[-1])
            self._victim = target_pnt
            return ModeInfo(Mode.KILL,target_pnt), kill_path
        else:
            logger.info("ROAM")
            roaming_points = self.get_roaming_point()
            for roam_point in roaming_points:
                roam_path = self.get_path(roam_point)
                if not roam_path:
                    continue
                target_pnt = Point(*roam_path[-1])
                return ModeInfo(Mode.ROAMING, target_pnt), roam_path
        return None, None

    def panic_path(self):
        safe_places = self.get_safe_place()
        logger.info(f"safe places: {safe_places}")
        safe_path_list = self.get_good_place(safe_places)
        logger.info(f"get {len(safe_path_list)} safe pathes")
        for sp in safe_path_list:
            if len(sp) > 1:
                next_point = Point(*sp[1])
                if not self.is_place_safe(next_point) and self.is_place_safe():
                    continue
                return sp
        return None

    def start_panic(self):
        logger.info("PANICCC!")
        self._mode = None
        self._panics += 1
        if self._panics > 4 and not self._is_bomb_placed:
            self._panics = 0
            return "ACT"
        panic_path = self.panic_path()
        if panic_path:
            next_p = Point(*panic_path[1])
            return self.get_direction(self._me,next_p)
        return "NULL"
        
    def get_next_mode_moves(self, new_path):
        next_point = Point(*new_path[1])
        dr = self.get_direction(self._me, next_point)
        logger.info(f"direct: {dr}")
        if self._mode.mode != Mode.PANIC:
            if len(new_path) == 2:
                prev_mode = self._mode.mode
                self._mode = None
                if prev_mode in DESTROY_MODES:
                    return NextMoves("ACT")
                else:
                    return NextMoves(dr)
            if len(new_path) < 5 or self._is_bomb_placed:
                return NextMoves(dr)
            current_points = self.get_potential_yield(self._me)
            next_points = self.get_potential_yield(next_point)
            logger.info(f"yields: {current_points}   {next_points}")
            if next_points > current_points:
                return NextMoves(dr, "ACT")
            elif current_points:
                return NextMoves("ACT", dr)
            else:
                return NextMoves(dr)
        return NextMoves("NONE")

    @get_deco
    def get(self, board_string):

        if self._board.get_at(*self._me.get()).get_char() == _ELEMENTS["DEAD_BOMBERMAN"]:
            self._prev_players_num = 0
            self._bomb_placed = -1000
            logger.info("game over")
            return "NULL"

        if len(self._other_players) > self._prev_players_num:
            self._victim = None
            self._panics = 0
            logger.info("restarted!!")

        new_path = None

        if not self._mode:
           self._mode, new_path  = self.pick_mode()
        elif self._mode.mode != Mode.PANIC:
            perks_path = self.get_near_perk_path()
            if perks_path:
                logger.info("PERK HUNT!!")
                target_pnt = Point(*perks_path[-1])
                new_path = perks_path
                self._mode = ModeInfo(Mode.PERK_HUNT,target_pnt)
            else:
                kill_path = self.get_kill_path()
                if self._mode.mode == Mode.ROAMING and kill_path:
                    logger.info("KILL!!")
                    target_pnt = Point(*kill_path[-1])
                    self._victim = target_pnt
                    self._mode = ModeInfo(Mode.KILL,target_pnt)
                    new_path = kill_path
                else:
                    new_path = self.get_path(self._mode.target)
                    if not new_path:
                        logger.info("Time to pick new mode")
                        self._mode, new_path  = self.pick_mode()
                        logger.info("new mode is {self._mode}")

        logger.info(f"Current mode is {self._mode}, {new_path}")

        
        if not self._mode or not new_path:
            return self.start_panic()
        else:
            self._panics = 0

        next_move = self.get_next_mode_moves(new_path)

        next_point = self.direction_to_point(next_move.direction)

        if self.is_place_safe(next_point):
            return str(next_move)
        elif self.is_place_safe():
            return "NONE"
        else:
            return self.start_panic()

if __name__ == '__main__':
    raise RuntimeError("This module is not intended to be ran from CLI")
