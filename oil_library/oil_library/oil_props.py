'''
OilProps class which serves as a wrapper around
gnome.db.oil_library.models.Oil class

It also contains a dict containing a small number of sample_oils if user does
not wish to query database to use an _r_oil.

It contains a function that takes a string for 'name' and returns and Oil
object used to initialize and OilProps object

Not sure at present if this needs to be serializable?
'''
import copy
from math import log, exp

from repoze.lru import lru_cache

import unit_conversion as uc
from .utilities import get_density, get_boiling_points_from_cuts, get_viscosity


def molecular_weight(bp, component):
    '''
    return the molecular weight of the pseudocomponents (mw_i) given the
    boiling points. It returns the mw_i for saturates and aromatic components
    '''
    if component == 'saturate':
        mw = (0.04132 - 1.985e-4 * bp + (9.494e-7 * bp ** 2))
    elif component == 'aromatic':
        mw = (0.04132 - 1.985e-4 * bp + (9.494e-7 * bp ** 2))

    return mw


class OilProps(object):
    '''
    Class which:
    - Contains an oil object.
    - Provides more sophisticated oil properties than the basic oil database
      object.
    - These properties are the result of calculations made upon the
      basic oil database properties.
    - Generally speaking, we will try to adhere to the ASTM standards and use
      the SI measurement system when determining units for our values.
      The SI base units are consistent with the MKS system.

    Specifically, OilProps has a few categories of properties:
    Density:
    - returns a scalar as opposed to a list of Densities.

    Viscosity:

    '''

    def __init__(self, oil_):
        '''
        Extends the raw Oil object to include properties required by
        weathering processes. If oil_ is not pulled from database or user may
        wish to use simple half life weatherer, in this case, there is no need
        to carry around more than one psuedo-component. Let user set max_cuts
        if desired, but only during initialization.

        :param oil_: Oil object that maps to entity in OilLib database
        :type oil_: Oil object
        '''
        self._r_oil = oil_

        # Default format for mass components:
        # mass_fraction =
        # [m0_s, m0_a, m1_s, m1_a, ..., m_resins, m_asphaltenes]
        self.mass_fraction = []
        self.boiling_point = []
        for bp, mf in get_boiling_points_from_cuts(oil_):
            self.mass_fraction.append(mf)
            self.boiling_point.append(bp)

        # set molecular weights
        self.molecular_weight = None
        self._component_mw()

        #self.bullwinkle = None
        #self.bulltime = None

    def __repr__(self):
        return ('{0.__class__.__module__}.{0.__class__.__name__}('
                'oil_={0._r_oil!r})'.format(self))

    name = property(lambda self: self._r_oil.name,
                    lambda self, val: setattr(self._r_oil, 'name', val))
    api = property(lambda self: self.get('api'))

    def get(self, prop):
        'get raw oil props'
        val = None
        try:
            val = getattr(self._r_oil, prop)
        except AttributeError:
            try:
                val = getattr(self._r_oil.imported, prop)
            except:
                pass

        return val

    @lru_cache(2)
    def get_density(self, temp=None, out=None):
        '''
        return density at a temperature
        do we want to do any unit conversions here?
        todo: memoize function

        :param temp: temperature in Kelvin. Could be an ndarray, list or scalar
        :type temp: scalar, list, tuple or ndarray - assumes it is in Kelvin
        '''
        if temp:
            return get_density(self._r_oil, temp, out)
        else:
            return uc.convert('density', 'API', 'kg/m^3', self.api)

    @lru_cache(2)
    def get_viscosity(self, temp=288.15, out=None):
        '''
        return viscosity at a temperature, default is viscosity at 15degC
        todo: memoize function

        :param temp: temperature in Kelvin. Could be an ndarray, list or scalar
        :type temp: scalar, list, tuple or ndarray - assumes it is in Kelvin
        '''
        return get_viscosity(self._r_oil, temp, out)

    def get_bulltime(self):
        '''
        return bulltime (time to emulsify)
        either user set or just return a flag
        '''
        # check for user input value, otherwise set to -999 as a flag
        bulltime = -999.

        return bulltime

    @property
    def num_components(self):
        return len(self.mass_fraction)

    def _component_mw(self):
        'estimate molecular weights of components'
        if self.boiling_point == [float('inf')] * len(self.boiling_point):
            # if there are only resins + asphaltenes, unclear how to set
            # molecular weight - we don't want an array of 'nan' values so
            # leave it as None
            return

        self.molecular_weight = [float('nan')] * self.num_components
        # self.molecular_weight = []

        for ix, bp in enumerate(self.boiling_point):
            if bp == float('inf'):
                # this should be the case for resins + asphaltenes so just
                # make the mw equal to the components with highest BP
                self.molecular_weight[ix] = self.molecular_weight[ix - 1]
                continue

            if ix % 2 == 0:
                self.molecular_weight[ix] = molecular_weight(bp, 'saturate')
                # self.molecular_weight.append(molecular_weight(bp, 'saturate'))
            else:
                # will define a different function for mw_aromatics
                self.molecular_weight[ix] = molecular_weight(bp, 'aromatic')
                # self.molecular_weight.append(molecular_weight(bp, 'aromatic'))

    @lru_cache(2)
    def vapor_pressure(self, temp, atmos_pressure=101325.0):
        '''
        water_temp and boiling point units are Kelvin
        returns the vapor_pressure in SI units (Pascals)
        todo: memoize function
        '''
        D_Zb = 0.97
        R_cal = 1.987  # calories

        Pi = []
        for bp in self.boiling_point:
            if bp == float('inf'):
                # make the exponential decay constant 0 so mass is unchanged
                Pi.append(0.0)
            else:
                D_S = 8.75 + 1.987 * log(bp)
                C_2i = 0.19 * bp - 18

                var = 1. / (bp - C_2i) - 1. / (temp - C_2i)
                ln_Pi_Po = D_S * (bp - C_2i) ** 2 / (D_Zb * R_cal * bp) * var
                Pi.append(exp(ln_Pi_Po) * atmos_pressure)

        return Pi

    def tojson(self):
        'for now, just convert underlying oil object to json'
        return self._r_oil.tojson()

    def __eq__(self, other):
        '''
        need to explicitly compare __dict__
        However, PyGnome initializes two OilProps object when invoked from the
        WebGnomeClient, there is an sql alchemy object embedded in _r_oil
        which maybe different. To avoid comparing the sqlalchemy object that
        is part of the raw oil record, this works as follows:

        1. check if self.__dict__ == other.__dict__
        2. if above fails, then check if the tojson() for both OilProps objects
        match. This assumes that both objects contain tojson()
        '''
        if type(self) != type(other):
            return False

        if self.__dict__ == other.__dict__:
            return True
        else:
            try:
                return self.tojson() == other.tojson()
            except Exception:
                return False

        return False

    def __ne__(self, other):
        return not self == other

    def __deepcopy__(self, memo):
        '''
        The _r_oil object should not be copied - it should just be referenced
        to create the OilProps copy. The database record itself does not need
        to be a deepcopy - both OilProps objects can reference the same
        database record
        '''
        c_op = self.__class__(self._r_oil)
        if c_op != self:
            '''
            Attributes are currently derived from _r_oil object. Unless the
            user changes 'mass_fractions', 'boiling_point', 'molecular_weight'
            after initialization, the two objects should be equal
            '''
            for attr in c_op.__dict__:
                if getattr(self, attr) != getattr(c_op, attr):
                    setattr(c_op, attr,
                            copy.deepcopy(getattr(self, attr), memo))
        return c_op
