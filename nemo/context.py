# Copyright (C) 2011, 2012, 2014 Ben Elliston
# Copyright (C) 2014, 2015, 2016 The University of New South Wales
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.

"""
Implementation of the Context class.

A simulation context encapsulates all simulation state ensuring that
there is never any residual state left behind after a simulation
run. It also allows multiple contexts to be compared after individual
simulation runs.
"""

import json
import re

import numpy as np
import pandas as pd
import pint

from nemo import configfile, costs, generators, polygons, regions
from nemo.nem import hourly_demand, hourly_regional_demand, startdate

ureg = pint.UnitRegistry()
ureg.default_format = '.2f~P'


class Context():
    """All simulation state is kept in a Context object."""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        """Initialise a default context."""
        self.verbose = False
        self.regions = regions.All
        self.startdate = startdate
        # Number of timesteps is determined by the number of demand rows.
        self.hours = len(hourly_regional_demand)
        # Estimate the number of years from the number of simulation hours.
        if self.hours == 8760 or self.hours == 8784:
            self.years = 1
        else:
            self.years = self.hours / (365.25 * 24)

        self.relstd = 0.002  # 0.002% unserved energy
        self.generators = [generators.CCGT(polygons.WILDCARD, 20000),
                           generators.OCGT(polygons.WILDCARD, 20000)]
        self.demand = hourly_demand.copy()
        self.timesteps = len(self.demand)
        self.spill = pd.DataFrame()
        self.generation = pd.DataFrame()
        self.unserved = pd.DataFrame()
        # System non-synchronous penetration limit
        self.nsp_limit = float(configfile.get('limits', 'nonsync-penetration'))
        self.costs = costs.NullCosts()

    def total_demand(self):
        """Return the total demand from the data frame."""
        return self.demand.values.sum()

    def unserved_energy(self):
        """Return the total unserved energy."""
        return self.unserved.values.sum()

    def surplus_energy(self):
        """Return total surplus energy."""
        return self.spill.values.sum()

    def unserved_percent(self):
        """
        Return the total unserved energy as a percentage of total demand.

        >>> import pandas as pd
        >>> c = Context()
        >>> c.unserved_percent()
        0.0
        >>> c.demand = pd.DataFrame()
        >>> c.unserved_percent()
        nan
        """
        # We can't catch ZeroDivision because numpy emits a warning
        # (which we would rather not suppress).
        if self.total_demand() == 0:
            return np.nan
        return self.unserved_energy() / self.total_demand() * 100

    def set_capacities(self, caps):
        """Set generator capacities from a list."""
        num = 0
        for gen in self.generators:
            for (setter, min_cap, max_cap) in gen.setters:
                # keep parameters within bounds
                newval = max(min(caps[num], max_cap), min_cap)
                setter(newval)
                num += 1
        # Check every parameter has been set.
        assert num == len(caps), '%d != %d' % (num, len(caps))

    def __str__(self):
        """Make a human-readable representation of the context."""
        string = ""
        if self.regions != regions.All:
            string += 'Regions: ' + str(self.regions) + '\n'
        if self.verbose:
            string += 'Generators:' + '\n'
            for gen in self.generators:
                string += '\t' + str(gen)
                summary = gen.summary(self)
                if summary is not None:
                    string += '\n\t   ' + summary + '\n'
                else:
                    string += '\n'
        string += 'Timesteps: %d h\n' % self.hours
        total_demand = (self.total_demand() * ureg.MWh).to_compact()
        string += 'Demand energy: {}\n'.format(total_demand)
        surplus_energy = (self.surplus_energy() * ureg.MWh).to_compact()
        string += 'Unused surplus energy: {}\n'.format(surplus_energy)
        if self.surplus_energy() > 0:
            spill_series = self.spill[self.spill.sum(axis=1) > 0]
            string += 'Timesteps with unused surplus energy: %d\n' % len(spill_series)

        if self.unserved.empty:
            string += 'No unserved energy'
        else:
            string += 'Unserved energy: %.3f%%' % self.unserved_percent() + '\n'
            if self.unserved_percent() > self.relstd * 1.001:
                string += 'WARNING: reliability standard exceeded\n'
            string += 'Unserved total hours: ' + str(len(self.unserved)) + '\n'

            # A subtle trick: generate a date range and then substract
            # it from the timestamps of unserved events.  This will
            # produce a run of time detlas (for each consecutive hour,
            # the time delta between this timestamp and the
            # corresponding row from the range will be
            # constant). Group by the deltas.
            rng = pd.date_range(self.unserved.index[0], periods=len(self.unserved.index), freq='H')
            unserved_events = [k for k, g in self.unserved.groupby(self.unserved.index - rng)]
            string += 'Number of unserved energy events: ' + str(len(unserved_events)) + '\n'
            if not self.unserved.empty:
                usmin = (self.unserved.min() * ureg.MW).to_compact()
                usmax = (self.unserved.max() * ureg.MW).to_compact()
                string += 'Shortfalls (min, max): ({}, {})'.format(usmin, usmax)
        return string

    class JSONEncoder(json.JSONEncoder):
        """A custom encoder for Context objects."""

        def default(self, o):
            """Encode a Context object into JSON."""
            if isinstance(o, Context):
                result = []
                for gen in o.generators:
                    tech = re.sub(r"<class 'generators\.(.*)'>",
                                  r'\1', str(type(gen)))
                    result += [{'label': gen.label, 'polygon': gen.polygon,
                                'capacity': gen.capacity, 'technology': tech}]
                return result
            return None
