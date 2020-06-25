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

ACT = "ACT"
NONE = "NONE"
UP = "UP"
DOWN = "DOWN"
LEFT = "LEFT"
RIGHT = "RIGHT"

class Mode(Enum):
    KILL = 1
    ROAMING = 2
    PANIC = 3
    PERK_HUNT = 4

class NextMoves:
    def __init__(self, *acts):
        self._moves = list(acts)
        if not self._moves:
            self._moves = []
        dr = list(filter(lambda x: x not in [ACT, NONE], self._moves))
        self.direction = dr[0] if dr else None

    def get_oppose_dr(self):
        oppose_dir = {
            LEFT: RIGHT,
            RIGHT: LEFT,
            DOWN: UP,
            UP: DOWN,
        }
        return oppose_dir.get(self.direction)

    def act(self):
        return ACT in self._moves

    def __str__(self):
        return ",".join(self._moves)

    def no_act(self):
        if self.act():
            self._moves.remove(ACT)

    def do_act(self, after_move = False):
        if len(self._moves) <= 1:
            if after_move:
                self._moves.append(ACT)
            else:
                self._moves.insert(0, ACT)


class Perk(Enum):
   IMMUNE = _ELEMENTS["BOMB_IMMUNE"]
   RC = _ELEMENTS["BOMB_REMOTE_CONTROL"]
   MULTI_BOMBS = _ELEMENTS["BOMB_COUNT_INCREASE"]
   RANGE = _ELEMENTS["BOMB_BLAST_RADIUS_INCREASE"]


PERK_DURATION = 30
RANGE_INC = 2

class PerkInfo:
    def __init__(self):
        self.reset()

    def reset(self):
        self.current_perks = defaultdict(int)
        self._prev_perks = {}
        self._range = 0

    def get(self, perk: Perk):
        return self.current_perks[perk]

    def use_rc(self):
        self.current_perks[Perk.RC] -= 1

    def get_range(self):
        return self._range

    def update(self, ds):
        perks_info = dict(zip(ds._perks, map(lambda x: ds._board.get_at(x.get_x(), x.get_y()).get_char(),
                                                ds._perks)))
        if ds._me in self._prev_perks:
            perk_pickedup = Perk(self._prev_perks[ds._me])
            logger.info(f"Perk picked up:{perk_pickedup}")
            if perk_pickedup == Perk.RANGE:
                self.current_perks[perk_pickedup] += PERK_DURATION
                self._range += RANGE_INC
            elif perk_pickedup == Perk.RC:
                self.current_perks[perk_pickedup] = 3
            else:
                self.current_perks[perk_pickedup] = PERK_DURATION

        for perk in list(self.current_perks.keys()):
            perk = Perk(perk)
            if perk == Perk.RC:
                continue
            self.current_perks[perk] -= 1
            if self.current_perks[perk] <= 0:
                if perk == Perk.RANGE:
                    self._range = 0
                del self.current_perks[perk]
        self._prev_perks = perks_info
        logger.info(f"Current perks: {self.current_perks} range: {self._range}")
        
BOMB_TIMEOUT = 5

class MyBombInfo:
    def __init__(self):
        self.reset()

    def reset(self):
        self._placed = 0
        self.rc_placed = False
        self.pnt = None
        self.danger = set()

    def __str__(self):
        return f"placed: {self._placed} coords: {self.pnt}, rc: {self.rc_placed}"

    def placed(self):
        return self._placed != 0 

    def rc(self):
        return self.rc_placed

    def update(self, ds):
        if self.rc_placed and ds._prev_move.act():
            logger.info("RC Detonated!")
            self.reset()
            return 

        if ds._board.get_at(ds._me.get_x(), ds._me.get_y()).get_char() == _ELEMENTS["BOMB_BOMBERMAN"]:
            self.pnt = ds._me
            self._placed = BOMB_TIMEOUT
        elif ds._prev_move.act():
            prev_pnt = ds.direction_to_point(ds._prev_move.get_oppose_dr())
            self._placed = BOMB_TIMEOUT
            self.pnt = prev_pnt

        if self._placed == BOMB_TIMEOUT and ds._perks_info.get(Perk.RC):
            self.rc_placed = True
            ds._perks_info.use_rc()
            self._placed = 0

        if not self.rc_placed:
            if self._placed != 0:
                self._placed -= 1
                if not self._placed:
                    self.pnt = None

        self.danger = self._danger_places(ds)

    def _danger_places(self, ds):
        points = set()
        if self.pnt:
            walls = set(ds._board.get_barriers())
            def f(pnt):
                if pnt in walls:
                   return True 
                points.add(pnt)
            ds._board.walk_in_bomb_range(self.pnt, ds._board.BLAST_RANGE + ds._perks_info.get_range(), f)
        return points

@dataclass
class Chopper:
    dir: str
    coords: Point

class ChoppersInfo:
    def __init__(self):
        self.reset()

    def reset(self):
        self._prev_choppers= set()
        self._choppers = set()
        self.mad_choppers = set()
        self.dead_choppers = set()
        self._predicted_moves = set()

    def update(self, ds):
        dead_choppers = set(ds._board.get_dead_choppers())
        self.mad_choppers = dead_choppers - self._choppers
        logger.debug(f"aaah Mad choppers: {self.mad_choppers}")
        self._choppers = set(ds._board.get_meat_choppers())
        predicted_moves = []
        chops_copy = self._choppers.copy()
        walls = ds._walls.union(ds._destroy_walls).union(ds._board.get_blasts()).union(ds._board.get_destroied_walls())
        predicted_moves = []
        for chop in self._choppers:
            chops_sur = chop.surrounding_pnts()
            possible_moves = [(pnt, pnt in self._prev_choppers) for pnt in chops_sur]
            possible_moves = list(filter(lambda x: x[1], possible_moves))
            if len(possible_moves) != 1:
                continue
            chops_copy.remove(chop)
            chopper_vec = chop - possible_moves[0][0]
            chopper_move = chop + Point(*chopper_vec)
            logger.debug(f"Choppper {chop} predicted pos: {chopper_move}")
            if chopper_move in walls:
                chopper_moves = [pnt for pnt in chop.surrounding_pnts() if pnt not in walls]
                logger.debug(f"chopper {chop} meet wall, possible moves: {chopper_moves}")
                predicted_moves += chopper_moves
            else:
                predicted_moves.append(chopper_move)
        

        logger.debug(f"Unpredicted choppers:{chops_copy}")
        for chop in chops_copy:
            chopper_moves = [pnt for pnt in chop.surrounding_pnts() if pnt not in walls]
            predicted_moves += chopper_moves
        logger.debug(f"Predicted moves:{predicted_moves}")
        self._predicted_moves = predicted_moves

        

        self._prev_choppers = self._choppers


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

VECTOR_TO_DIR = {
    Point(0,1): DOWN,
    Point(0,-1): UP,
    Point(-1,0): LEFT,
    Point(1,0): RIGHT,
}

DIR_TO_VECTOR = {
    DOWN:Point(0,1),
    UP: Point(0,-1),
    LEFT: Point(-1,0),
    RIGHT: Point(1,0),
}

class DirectionSolver:
    """ This class should contain the movement generation algorithm."""

    def __init__(self):
        self._direction = None
        self._board = None
        self._last = None
        self._victim = None
        self._count = 0
        self._me = None
        self._bomb = MyBombInfo()
        self._prev_bombermans = set()
        self._target_point = None
        self._mode = None
        self._prev_players_num = 0
        self._panics = 0
        self.choppers = ChoppersInfo()
        self._perks_info = PerkInfo()
        self._prev_move = NextMoves()
        self._walls = set()
    
    @staticmethod
    def get_direction(pnt_from, pnt_to):
        vec = Point(pnt_to.get_x() - pnt_from.get_x(), pnt_to.get_y() - pnt_from.get_y())
        return VECTOR_TO_DIR.get(vec)

    @staticmethod
    def check_path_straight(path):
        assert path
        first = path[0]
        return all((first[0] == x[0] for x in path)) or \
               all((first[1] == x[1] for x in path))

        

    def direction_to_point(self, dr):
        return self._me + DIR_TO_VECTOR.get(dr)

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
        is_immune = self._perks_info.get(Perk.IMMUNE) > 1
        return \
               (is_immune or place not in self._future_blasts) and \
               place not in self.choppers.mad_choppers and \
               place not in self._next_choppers_moves
               #place not in self._board.get_barriers() and \
               #place not in self.choppers._choppers and \

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
        for dx, dy in product(range(-radius, radius+1), range(-radius, radius)):
            place = Point(self._me.get_x() + dx, self._me.get_y()+dy)
            #place not in self._board.get_barriers() and \
            if not place.is_bad(self._board._size) and \
                place not in self.choppers._predicted_moves and \
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
            if pnt.is_bad(self._board._size):
                return True, 0
            if pnt in self.choppers.mad_choppers:
                return True, 10
            el = self._board.get_at(pnt.get_x(), pnt.get_y())
            return el in break_el, points.get(el.get_char(), 0)
        blast_range = BLAST_RANGE + self._perks_info.get_range()
        ranges = [range(1, blast_range+1), range(-1, -blast_range-1 , -1)]
        for rg in ranges:
            for dx in rg:
                pnt = Point(current_point.get_x()+dx, current_point.get_y())
                brk, _pnts = get_points(pnt)
                pnts += _pnts
                if brk:
                    break
            for dy in rg:
                pnt = Point(current_point.get_x(), current_point.get_y()+dy)
                _pnts = get_points(pnt)
                brk, _pnts = get_points(pnt)
                pnts += _pnts
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
        if not walls_dens:
            return 
        qd  = walls_dens[0][0]
        points = walls_dens[0][1]
        logger.info(qd)
        logger.info(points)
        points = sorted(points, key = lambda x: x.distance(self._me), reverse=True)
        logger.info(points)
        logger.info(list(map(lambda x: x.distance(self._me),points)))
        return points
    
    def get_potential_chopper_moves(self):
        return self.choppers._predicted_moves
        mad_choppers_moves = []
        for mad_chopper in sefl.choppers.mad_choppers:
            for pnt in mad_chopper.surrounding_pnts():
                if not pnt.is_bad(self._board._size):
                    mad_choppers_moves.append(pnt)

        choppers = self.choppers._choppers.union(mad_choppers_moves)
        ch_moves = []
        for chopper in choppers:
            for pnt in chopper.surrounding_pnts():
                if not pnt.is_bad(self._board._size) and \
                    self._board.get_at(*pnt.get()).get_char() not in [_ELEMENTS["DESTROY_WALL"], _ELEMENTS["WALL"]]:
                    ch_moves.append(pnt)
        return ch_moves

    def get_near_perks(self):
        PERK_RADIUS = 8
        logger.debug(f"Perks: {self._perks}")
        #perks = filter(lambda x: self._board.get(x).get_char() != _ELEMENTS["BOMB_REMOTE_CONTROL"],  self._perks)
        perks = self._perks
        perks = sorted(list(filter(lambda x: self._me.distance(x) <= PERK_RADIUS, perks)), key = lambda x: x.distance(self._me))
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
            matrix[ch_move.get_y()][ch_move.get_x()]+= 5000

        future_blasts = self._board.get_future_blasts()
        if self._perks_info.get(Perk.IMMUNE) < 4:
            for fb in future_blasts.union(self._bomb.danger):
                matrix[fb.get_y()][fb.get_x()] *= 10

        future_blasts = self._board.get_future_blasts(True)
        if self._bomb.placed == 2:
            future_blasts = future_blasts.union(self._bomb.danger)

        self._future_blasts = future_blasts
        if self._perks_info.get(Perk.IMMUNE) < 4:
            for fb in self._future_blasts:
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
            self._me = board.get_bomberman()
            self._destroy_walls = set(board.get_destroy_walls())
            self._walls = set(board.get_walls())

            self._other_players = board.get_other_bombermans()
            self._perks = board.get_perks()
            self._perks_info.update(self)
            self._bomb.update(self)
            self.choppers.update(self)

            self._matrix = self._make_matrix()
            logger.info(self._board.to_string())
            logger.debug(f"Bomb info: {self._bomb}")
            res = NextMoves()
            try:
                res = f(self, board_string)
            except Exception as e:
                exc_info = traceback.format_exc()
                logger.error(f"Unexpected exception occured: {exc_info}")
            self._prev_bombermans = self._other_players
            self._prev_players_num = len(self._other_players)
            self._prev_perks = self._perks
            logger.info(f"send command: --->{res}<--- decision time: {time.time() - start_time} seconds")
            self._prev_move = res
            return str(res)
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
        if self._panics > 4 and not self._bomb.placed():
            self._panics = 0
            return NextMoves(ACT)
        panic_path = self.panic_path()
        logger.debug(f"Panic path: {panic_path}")
        if panic_path:
            next_p = Point(*panic_path[1])
            return NextMoves(self.get_direction(self._me,next_p))
        return NextMoves()
        
    def get_next_mode_moves(self, new_path):
        next_point = Point(*new_path[1])
        dr = self.get_direction(self._me, next_point)
        logger.info(f"direct: {dr}")
        if self._mode.mode != Mode.PANIC:
            self._panics = 0
            path_is_straight = self.check_path_straight(new_path)
            place_bomb = self._mode.mode in DESTROY_MODES and \
                         path_is_straight and \
                         len(new_path) - 1 <= BLAST_RANGE + self._perks_info.get_range()
            if len(new_path) == 2 or place_bomb:
                prev_mode = self._mode.mode
                self._mode = None
                if prev_mode in DESTROY_MODES:
                    panic_path = self.panic_path()
                    dr = "NONE"
                    if panic_path:
                        next_point = Point(*panic_path[1])
                        dr = self.get_direction(self._me, next_point)
                    return NextMoves(ACT, dr)
                else:
                    return NextMoves(dr)
            SAFE_MOVES = 5

            if len(new_path) <= SAFE_MOVES and self._perks_info.get(Perk.IMMUNE) <= SAFE_MOVES:
                return NextMoves(dr)

            if self._perks_info.get(Perk.MULTI_BOMBS):
                return NextMoves(ACT, dr)

            if self._bomb.placed():
                return NextMoves(dr)


            current_points = self.get_potential_yield(self._me)
            next_points = self.get_potential_yield(next_point)
            logger.info(f"yields: {current_points}   {next_points}")
            if next_points > current_points:
                return NextMoves(dr, ACT)
            elif current_points:
                return NextMoves(ACT, dr)
            else:
                return NextMoves(dr)

    @get_deco
    def get(self, board_string):

        if self._board.get_at(*self._me.get()).get_char() == _ELEMENTS["DEAD_BOMBERMAN"] or \
           not self._destroy_walls:
            logger.info("game over")
            self._prev_players_num = 0
            self._bomb.reset()
            self._perks_info.reset()
            self._victim = None
            self._panics = 0
            return NextMoves()

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
                    target_obj = self._board.get(self._mode.target).get_char()
                    if not new_path or target_obj not in [_ELEMENTS["OTHER_BOMBERMAN"], _ELEMENTS["DESTROY_WALL"] ]:
                        logger.info("Time to pick new mode")
                        self._mode, new_path  = self.pick_mode()
                        logger.info(f"new mode is {self._mode}")

        logger.info(f"Current mode is {self._mode}, {new_path}")

        
        if not self._mode or \
           not new_path or \
           all([pnt in self._bomb.danger for pnt in new_path]):
            return self.start_panic()
        else:
            self._panics = 0

        next_move = self.get_next_mode_moves(new_path)
        logger.debug(f"Next moves returned: {next_move}")
        next_point = self.direction_to_point(next_move.direction)
        is_immune = self._perks_info.get(Perk.IMMUNE)

        if self._bomb.rc_placed:
            next_move.no_act()
            if is_immune:
                next_move.do_act(after_move = False)
            elif self._me == self._bomb.pnt:
                pass
            elif next_point not in self._bomb.danger:
                next_move.do_act(after_move = True)
            elif self._me not in self._bomb.danger:
                next_move.do_act(after_move = False)
        logger.debug(f"Next moves with rc: {next_move}")

        if self.is_place_safe(next_point):
            return next_move
        elif self.is_place_safe():
            return NextMoves()
        else:
            return self.start_panic()

if __name__ == '__main__':
    raise RuntimeError("This module is not intended to be ran from CLI")
