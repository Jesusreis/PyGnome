'''
oil removal from various cleanup options
add these as weatherers
'''
from datetime import timedelta
import copy
import os

import numpy as np
from colander import (SchemaNode, Float, String)

from gnome.basic_types import oil_status
from gnome.weatherers import Weatherer
from gnome.utilities.serializable import Serializable, Field

from .core import WeathererSchema
from .. import _valid_units

import unit_conversion as uc


class SkimmerSchema(WeathererSchema):
    amount = SchemaNode(Float())
    units = SchemaNode(String())
    efficiency = SchemaNode(Float())


class Skimmer(Weatherer, Serializable):
    _state = copy.deepcopy(Weatherer._state)
    _state += [Field('amount', save=True, update=True),
               Field('units', save=True, update=True),
               Field('efficiency', save=True, update=True)]

    _schema = SkimmerSchema

    # todo: following is same as Spill code so rework to make it DRY
    valid_vol_units = _valid_units('Volume')
    valid_mass_units = _valid_units('Mass')

    def __init__(self,
                 amount,
                 units,
                 efficiency,
                 active_start,
                 active_stop,
                 **kwargs):
        '''
        initialize Skimmer object - calls base class __init__ using super()
        active_start and active_stop time are required
        '''
        self._units = None
        self.amount = amount
        self.units = units
        self.efficiency = efficiency

        super(Skimmer, self).__init__(active_start=active_start,
                                      active_stop=active_stop,
                                      **kwargs)

        # get the rate as amount/sec, use this to compute amount at each step
        self._rate = self.amount/(self.active_stop -
                                  self.active_start).total_seconds()
        # let prepare_for_model_step set timestep to use when active_start or
        # active_stop is between a timestep. Generally don't do subtimestep
        # resolution; however, in this case we want numbers to add up correctly
        self._timestep = 0.0

        if self.units is None:
            raise TypeError('Need valid mass or volume units for amount')

    def _validunits(self, value):
        'checks if units are either valid_vol_units or valid_mass_units'
        if value in self.valid_vol_units or value in self.valid_mass_units:
            return True
        return False

    @property
    def units(self):
        'return units for amount skimmed'
        return self._units

    @units.setter
    def units(self, value):
        if self._validunits(value):
            self._units = value
        else:
            msg = ('{0} are not valid volume or mass units.'
                   ' Not updated').format('value')
            self.logger.warn(msg)

    def prepare_for_model_run(self, sc):
        if sc.spills:
            sc.weathering_data['skimmed'] = 0.0

    def prepare_for_model_step(self, sc, time_step, model_time_datetime):
        '''
        Do sub timestep resolution here so numbers add up correctly
        Mark LEs to be skimmed - do them in order right now. Assume all LEs
        that are released together will be skimmed together since they would
        be closer to each other in position.

        Assumes: there is more mass in water than amount of mass to be
        skimmed. The LEs marked for Skimming are marked only once -
        code checks to see if any LEs are marked for skimming and if
        none are found, it marks them.
        '''
        if not self.on:
            self._active = False
            return

        self._timestep = time_step
        dt = timedelta(seconds=time_step)

        if (model_time_datetime + dt > self.active_start and
            self.active_stop > model_time_datetime):
            self._active = True

            if (model_time_datetime < self.active_start):
                self._timestep = \
                    time_step - (self.active_start -
                                 model_time_datetime).total_seconds()

            if (self.active_stop < model_time_datetime + dt):
                self._timestep = (self.active_stop -
                                  model_time_datetime).total_seconds()

            if (sc['status_codes'] == oil_status.skim).sum() == 0:
                'Need to mark LEs for skimming'
                substance = sc.get_substances(complete=False)
                if len(substance) > 1:
                    msg = ('Found more than one type of Oil - not supported. '
                           'Results will be incorrect')
                    self.logger.error(msg)
                substance = substance[0]
                total_mass_removed = self._get_mass(substance, self.amount)
                total_mass_removed *= self.efficiency
                data = sc.substancedata(substance, ['status_codes', 'mass'])

                if total_mass_removed >= data['mass'].sum():
                    data['status_codes'][:] = oil_status.skim
                else:
                    # sum up mass until threshold is reached, find index where
                    # total_mass_removed is reached or exceeded
                    ix = np.where(np.cumsum(data['mass']) >=
                                  total_mass_removed)[0][0] + 1
                    data['status_codes'][:ix] = oil_status.skim

                sc.update_from_substancedata(self._arrays, substance)

        else:
            self._active = False

    def _get_mass(self, substance, amount):
        '''
        return 'amount' in units of 'kg' for specified substance
        '''
        if self.units in self.valid_mass_units:
            rm_mass = uc.convert('Mass', self.units, 'kg', amount)
        else:   # amount must be in volume units
            rm_vol = uc.convert('Volume', self.units, 'm^3', amount)
            rm_mass = substance.get_density() * rm_vol

        return rm_mass

    def _mass_to_remove(self, substance):
        '''
        use density at 15C, ie corresponding with API to do mass/volume
        conversion
        '''
        amount = self._rate * self._timestep
        rm_mass = self._get_mass(substance, amount)

        return rm_mass

    def weather_elements(self, sc, time_step, model_time):
        '''
        Assumes there is only ever 1 substance being modeled!
        remove mass equally from LEs marked to be skimmed
        '''
        if not self.active:
            return

        if len(sc) == 0:
            return

        for substance, data in sc.itersubstancedata(self._arrays):
            rm_mass = (self._mass_to_remove(substance) * self.efficiency)

            self.logger.info('{0} - Amount skimmed: {1}'.
                             format(os.getpid(), rm_mass))

            # following should work even if all elements go to zero so
            # ~c_to_zero is all False since rm_mass_per_c is a scalar
            mask = data['status_codes'] == oil_status.skim
            rm_mass_frac = rm_mass / data['mass'][mask].sum()
            data['mass_components'][mask, :] = \
                (1 - rm_mass_frac) * data['mass_components'][mask, :]
            data['mass'][mask] = data['mass_components'][mask, :].sum(1)

            sc.weathering_data['skimmed'] += rm_mass

        sc.update_from_substancedata(self._arrays)


class Burn(Weatherer, Serializable):
    _state = copy.deepcopy(Weatherer._state)
    _schema = WeathererSchema

    def prepare_for_model_run(self, sc):
        if sc.spills:
            sc.weathering_data['burned'] = 0.0

    def weather_elements(self, sc, time_step, model_time):
        'for now just take away 0.1% at every step'
        if self.active and len(sc) > 0:
            for substance, data in sc.itersubstancedata(self._arrays):
                mask = data['status_codes'] == oil_status.in_water
                # take out 0.25% of the mass
                pct_per_le = (1 - 0.25/data['mass_components'].shape[1])
                mass_remain = pct_per_le * data['mass_components'][mask, :]
                sc.weathering_data['burned'] += \
                    np.sum(data['mass_components'][mask, :] - mass_remain[:, :])
                data['mass_components'][mask, :] = mass_remain
                data['mass'][mask] = data['mass_components'][mask, :].sum(1)

            sc.update_from_substancedata(self._arrays)


class Dispersion(Weatherer, Serializable):
    _state = copy.deepcopy(Weatherer._state)
    _schema = WeathererSchema

    def prepare_for_model_run(self, sc):
        if sc.spills:
            sc.weathering_data['dispersed'] = 0.0

    def weather_elements(self, sc, time_step, model_time):
        'for now just take away 0.1% at every step'
        if self.active and len(sc) > 0:
            for substance, data in sc.itersubstancedata(self._arrays):
                mask = data['status_codes'] == oil_status.in_water
                # take out 0.25% of the mass
                pct_per_le = (1 - 0.015/data['mass_components'].shape[1])
                mass_remain = pct_per_le * data['mass_components'][mask, :]
                sc.weathering_data['dispersed'] += \
                    np.sum(data['mass_components'][mask, :] - mass_remain[:, :])
                data['mass_components'][mask, :] = mass_remain
                data['mass'][mask] = data['mass_components'][mask, :].sum(1)

            sc.update_from_substancedata(self._arrays)