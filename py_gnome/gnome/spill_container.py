#!/usr/bin/env python
"""
spill_container.py

Implements a container for spills -- keeps all the data from each spill in one
set of arrays. The spills themselves provide some of the arrays themselves
(adding more each time LEs are released).
"""
import os
from collections import namedtuple

import numpy
np = numpy

from gnome.basic_types import oil_status
from gnome.array_types import (positions,
                               next_positions,
                               last_water_positions,
                               status_codes,
                               spill_num,
                               id,
                               mass,
                               age,
                               density,
                               substance,
                               ArrayType)

from gnome.utilities.orderedcollection import OrderedCollection
import gnome.spill
from gnome import AddLogger


# Organize information about spills per substance
# 1. substances: list of substances
# 2. spills: list of lists spills. Each element are list of spills
#    corresponding w/ substance
# 3. data: A list of dict's where each dict is the data_array corresponding
#    with the substance for elements released thus far. This is a copy of the
#    internally stored data_arrays.
#
# if more than one type of substance in multiple spills, then label the
# substances as index into 'substances' list.
substances_spills = namedtuple('substances_spills',
                               ['substances',
                                'spills',
                                'data'])


class SpillContainerData(object):
    """
    A really simple SpillContainer -- holds the data arrays,
    but doesn't manage spills, etc.

    Think of it as a read-only SpillContainer.

    Designed primarily to hold data retrieved from cache
    """
    def __init__(self, data_arrays=None, uncertain=False):
        """
        Initialize a SimpleSpillContainer.

        :param uncertain=False: flag indicating whether this holds uncertainty
                                elements or not
        :param data_arrays=None: A dict of all the data arrays you want to hold
                                 NOTE: no error checking! they should be
                                       correctly aligned, etc.

        The common use-case for this is for loading from cache for
        re-rendering, etc.

        Note: initialize current_time_stamp attribute to None. It is
        responsibility of caller to set current_time_stamp (for eg: Model)
        """
        self.uncertain = uncertain

        # sets whether the spill is active or not
        self.on = True

        if not data_arrays:
            data_arrays = {}

        self._data_arrays = data_arrays
        self.current_time_stamp = None
        self.weathering_data = {}

        # following internal variable is used when comparing two SpillContainer
        # objects. When testing the data arrays are equal, use this tolerance
        # with numpy.allclose() method. Default is to make it 0 so arrays must
        # match exactly. This will not be true when _state is stored midway
        # through the run since positions are stored as single dtype as opposed
        # to double
        self._array_allclose_atol = 0

    def __contains__(self, item):
        return item in self._data_arrays

    def __getitem__(self, data_name):
        """
        The basic way to access data for the LEs

        :param data_name: the name of the array to be returned

        example:  a_spill_container['positions'] give you the
                  (x,y,z positions array of the elements)

        :raises KeyError: raised if the data is not there
        """
        return self._data_arrays[data_name]

    def __setitem__(self, data_name, array):
        """
        sets the data item

        careful! -- this should probably only be used for testing!
        as all arrays need to be compatible

        It will be checked to at least be size-consistent with the rest of the
        data, and type-consistent if the data array is being replaced

        It will not allow user to add a new data_array - only existing
        data_arrays can be modified.
        All data_arrays are defined in prepare_for_model_run
        """
        array = np.asarray(array)

        if data_name in self._data_arrays:
            # if the array is already here, the type should match
            if array.dtype != self._data_arrays[data_name].dtype:
                raise ValueError('new data array must be the same type')

            # and the shape should match
            if array.shape != self._data_arrays[data_name].shape:
                msg = 'data array must be the same shape as original array'
                raise ValueError(msg)
        else:
            # make sure length(array) equals length of other data_arrays.
            # check against one key
            if array.shape == ():
                raise TypeError('0-rank arrays are not valid. '
                                'If new data is a scalar, '
                                'enter a list [value]')

            if (len(array) != len(self)):
                raise IndexError('length of new data should match length of '
                                 'existing data_arrays.')

        self._data_arrays[data_name] = array

    def __eq__(self, other):
        'Compare equality of two SpillContanerData objects'
        if type(self) != type(other):
            return False

        if len(self.__dict__) != len(other.__dict__):
            return False

        # check key/val that are not dicts
        val_is_dict = []
        for key, val in self.__dict__.iteritems():
            'compare dict not including _data_arrays'
            if isinstance(val, dict):
                val_is_dict.append(key)
            elif key == '_substances_spills':
                '''
                this is just another view of the data - no need to write extra
                code to check equality for this
                '''
                pass
            elif val != other.__dict__[key]:
                return False

        # check key, val that are dicts
        for item in val_is_dict:
            if set(self.__dict__[item]) != set(other.__dict__[item]):
                # dicts should contain the same keys
                return False

            for key, val in self.__dict__[item].iteritems():
                other_val = other.__dict__[item][key]
                if isinstance(val, np.ndarray):
                    try:
                        if not np.allclose(val, other_val, 0,
                                           self._array_allclose_atol):
                            return False
                    except TypeError:
                        # not implemented for this dtype, so just check equality
                        if not np.all(val == other_val):
                            return False
                else:
                    if val != other_val:
                        return False

        return True

    def __ne__(self, other):
        return not (self == other)

    def __len__(self):
        """
        The "length" of a spill container is the number of elements in it.
        The first dimension of any ndarray in our data_arrays
        will always be the number of elements that are contained in a
        SpillContainer.
        """
        try:
            # find the length of an arbitrary first array
            return len(self._data_arrays.itervalues().next())
        except StopIteration:
            return 0

    @property
    def num_released(self):
        """
        The number of elements currently in the SpillContainer

        If SpillContainer is initialized, all data_arrays exist as ndarrays
        even if no elements are released.  So this will always return a valid
        int >= 0.
        """
        return len(self)

    @property
    def data_arrays(self):
        'Returns a dict of the all the data arrays'
        # this is a property in case we want change the internal implementation
        return self._data_arrays


class SpillContainer(AddLogger, SpillContainerData):
    """
    Container class for all spills -- it takes care of capturing the released
    LEs from all the spills, putting them all in a single set of arrays.

    Many of the "fields" associated with a collection of elements are optional,
    or used only by some movers, so only the ones required will be requested
    by each mover.

    The data for the elements is stored in the _data_arrays dict. They can be
    accessed by indexing. For example:

    positions = spill_container['positions'] : returns a (num_LEs, 3) array of
    world_point_types
    """
    def __init__(self, uncertain=False):
        super(SpillContainer, self).__init__(uncertain=uncertain)
        self.spills = OrderedCollection(dtype=gnome.spill.Spill)
        self.spills.register_callback(self._spills_changed,
                                      ('add', 'replace', 'remove'))
        self.rewind()

        # don't want user to add to array_types in middle of run. Since its
        # not possible to throw an error in this case, let's just make it a
        # bit difficult to do.
        # dict must be updated via prepar_for_model_run() only at beginning of
        # run. Make self._array_types an an instance variable
        self._reset_arrays()

        # Initialize following either the first time it is used or in
        # prepare_for_model_run() -- it could change with each new spill
        self._substances_spills = None
        self._oil_comp_array_len = None

    def __setitem__(self, data_name, array):
        """
        Invoke base class __setitem__ method so the _data_array is set
        correctly.  In addition, create the appropriate ArrayType if it wasn't
        created by the user.
        """
        super(SpillContainer, self).__setitem__(data_name, array)
        if data_name not in self._array_types:
            shape = self._data_arrays[data_name].shape[1:]
            dtype = self._data_arrays[data_name].dtype.type

            self._array_types[data_name] = ArrayType(shape, dtype)

    def _reset_arrays(self):
        '''
        reset _array_types dict so it contains default keys/values
        '''
        gnome.array_types.reset_to_defaults(['spill_num', 'id'])

        self._array_types = {'positions': positions,
                             'next_positions': next_positions,
                             'last_water_positions': last_water_positions,
                             'status_codes': status_codes,
                             'spill_num': spill_num,
                             'id': id,
                             'mass': mass,
                             'age': age}
        self._data_arrays = {}
        self._substances_spills = None

        # reset following since arrays are reset
        self._oil_comp_array_len = None

    def _set_substancespills(self):
        '''
        _substances could change when spills are added/deleted
        using _spills_changed callback to reset self._substance_spills to None
        If 'substance' is None, we still include it in this data structure -
        all spills that are 'on' are included. A spill that is off isn't really
        being modeled so ignore it.

        .. note::
            Should not be called in middle of run. prepare_for_model_run()
            will invoke this if self._substance_spills is None. This is another
            view of the data - it doesn't contain any state that needs to be
            persisted.
        '''
        subs = []
        spills = []
        num_rel = []
        if self._oil_comp_array_len is None:
            self._oil_comp_array_len = 1

        for spill in self.spills:
            if not spill.on:
                continue
            new_subs = spill.get('substance')
            if new_subs in subs:
                # substance already defined for another spill
                ix = subs.index(new_subs)
                spills[ix].append(spill)
                num_rel[ix] += spill.get('num_released')
            else:
                # new substance not yet defined
                num_rel.append(spill.get('num_released'))
                subs.append(new_subs)
                spills.append([spill])

                # also set _oil_comp_array_len to substance with most
                # components? -- *not* being used right now, but make it so
                # it works correctly for testing multiple substances
                if (hasattr(new_subs, 'num_components') and
                    new_subs.num_components > self._oil_comp_array_len):
                    self._oil_comp_array_len = new_subs.num_components

        # 'data' will be updated when weatherers ask for arrays they need
        # define the substances list and the list of spills for each substance
        self._substances_spills = substances_spills(substances=subs,
                                                    spills=spills,
                                                    data=[{}] * len(subs))

        if len(self.get_substances()) > 1:
            # add an arraytype for substance if more than one substance
            self._array_types.update({'substance': substance})
        elif len(self.get_substances()) == 1:
            # only one substance so reference the _data_arrays dict directly
            self._substances_spills.data[0] = self._data_arrays

        self.logger.info('{0} - number of substances: {1}'.
                         format(os.getpid(), len(self.get_substances())))

    def _update_substance_array_reset_data(self,
                                           subs_idx,
                                           num_rel_by_substance):
        '''
        -. update 'substance' array if more than one substance present. The
        value of array is the index of 'substance' in _substances_spills
        data structure

        -. reset 'data' dict for this substance if the 'data' is a copy of
        the data_arrays since the copy is out of sync with _data_arrays and
        must be remade.

        .. note::
            If there is only one substance in _substances_spills
            structure, then do nothing.
        '''
        if len(self.get_substances()) > 1:
            if num_rel_by_substance > 0:
                self['substance'][-num_rel_by_substance:] = subs_idx

            self._substances_spills.data[subs_idx] = {}

    def _spills_changed(self, *args):
        '''
        call back called on spills add/delete/replace
        Callback simply resets the internal _substance_spills attribute to None
        since the old _substance_spills value could now be invalid.
        '''
        self._substances_spills = None

    def _index_of_substance(self, substance):
        try:
            return self._substances_spills.substances.index(substance)
        except ValueError:
            'substance is not in list'
            self.logger.debug('{0} - Substance named: {1}, not found in data '
                              'structure'.format(os.getpid(), substance.name))
            return None

    def substancedata(self, substance, arrays):
        'return the data for specified substance'
        if self._substances_spills is None:
            # todo: figure out if we need this check everywhere
            self._set_substancespills()

        ix = self._index_of_substance(substance)

        if ix is None:
            return

        if len(self.get_substances()) > 1:
            self._set_substancedata(arrays)

        return self._substances_spills.data[ix]

    def iterspillsbysubstance(self):
        '''
        iterate through the substances spills datastructure and return the
        spills associated with each substance. This is used by release_elements
        DataStructure contains all spills. If some spills contain None for
        substance, these will be returned
        '''
        if self._substances_spills is None:
            self._set_substancespills()
        return self._substances_spills.spills

    def itersubstancedata(self, arrays):
        '''
        iterates through and returns the following for each iteration:
        (substance, substance_data)

        This is used by weatherers - if a substance is None, omit it from
        the iteration.

        :param arrays: list of array names that should be in the data.
        :returns: (substance, substance_data) for each iteration
            substance: substance object
            substance_data: dict of numpy arrays associated with substance
        '''
        if self._substances_spills is None:
            self._set_substancespills()

        if len(self.get_substances()) > 1:
            self._set_substancedata(arrays)
        return filter(lambda x: x[0] is not None,
                      zip(self._substances_spills.substances,
                          self._substances_spills.data))

    def update_from_substancedata(self, arrays, substance=None):
        '''
        let's only update the arrays that were changed
        only update if a copy of 'data' exists. This is the case if there are
        more then one substances
        '''
        if len(self.get_substances()) == 1:
            return
        if substance is None:
            self._update_all_from_substancedata(arrays)
        else:
            ix = self._index_of_substance(substance)
            if ix is None:
                return
            data = self._substances_spills.data[ix]
            mask = self['substance'] == ix
            for array in arrays:
                self[array][mask] = data[array][:]

    def _update_all_from_substancedata(self, arrays):
        for ix, data in enumerate(self._substances_spills.data):
            if self._substances_spills.substances[ix] is not None:
                mask = self['substance'] == ix
                for array in arrays:
                    self[array][mask] = data[array][:]

    def _set_substancedata(self, arrays):
        '''
        - update substance data, create a list of strided arrays
        for now only weathering data cares about this view so if 'substance' is
        None, then don't bother updating 'data'
        '''
        for ix, data in enumerate(self._substances_spills.data):
            if self._substances_spills.substances[ix] is None:
                continue

            mask = self['substance'] == ix
            for array in arrays:
                if array not in data:
                    data[array] = self[array][mask]

    def get_substances(self, complete=True):
        '''
        return substances stored in _substances_spills structure.
        Include None if complete is True. Default is complete=True.
        '''
        if self._substances_spills is None:
            self._set_substancespills()

        if complete:
            return self._substances_spills.substances
        else:
            return filter(None, self._substances_spills.substances)

    @property
    def array_types(self):
        """
        user can modify ArrayType initial_value in middle of run. Changing
        the shape should throw an error. Change the dtype at your own risk.
        This returns a new dict so user cannot add/delete an ArrayType in
        middle of run. Use prepare_for_model_run() to do add an ArrayType.
        """
        return dict(self._array_types)

    def rewind(self):
        """
        In the rewind operation, we:
        - rewind all the spills
        - restore _array_types to contain only defaults
          - movers/weatherers could have been deleted and we don't want to
            carry associated data_arrays
          - prepare_for_model_run() will be called before the next run and
            new arrays can be given

        - purge the data arrays
          - we gather data arrays for each contained spill
          - the stored arrays are cleared, then replaced with appropriate
            empty arrays
        """
        for spill in self.spills:
            spill.rewind()
        # create a full set of zero-sized arrays. If we rewound, something
        # must have changed so let's get back to default _array_types
        self._reset_arrays()
        self.initialize_data_arrays()
        self.weathering_data = {}  # reset to empty array
        self.logger.info('{0} - rewound SpillContainer'.format(os.getpid()))

    def get_spill_mask(self, spill):
        return self['spill_num'] == self.spills.index(spill)

    def uncertain_copy(self):
        """
        Returns a copy of the spill_container suitable for uncertainty

        It has all the same spills, with the same ids, and the uncertain
        flag set to True
        """
        u_sc = SpillContainer(uncertain=True)
        for sp in self.spills:
            u_sc.spills += sp.uncertain_copy()

        return u_sc

    def prepare_for_model_run(self, array_types={}):
        """
        called when setting up the model prior to 1st time step
        This is considered 0th timestep by model

        Make current_time optional since SpillContainer doesn't require it
        especially for 0th step; however, the model needs to set it because
        it will write_output() after each step. The data_arrays along with
        the current_time_stamp must be set in order to write_output()

        :param model_start_time: model_start_time to initialize
            current_time_stamp. This is the time_stamp associated with 0-th
            step so initial conditions for data arrays
        :param array_types: a dict of additional array_types to append to
            standard array_types attribute. The data_arrays are initialized and
            appended based on the values of array_types attribute

        .. note:: The SpillContainer cycles through each of the keys in
        array_types and checks to see if there is an associated initializer
        in each Spill. If a corresponding initializer is found, it gets the
        array_types from initializer and appends them to its own list. This was
        added for the case where 'droplet_diameter' array is defined/used by
        initializer (InitRiseVelFromDropletSizeFromDist) and we would like to
        see it in output, but no Mover/Weatherer needs it.
        """
        # Question - should we purge any new arrays that were added in previous
        # call to prepare_for_model_run()?
        # No! If user made modifications to _array_types before running model,
        # let's keep those. A rewind will reset data_arrays.
        self._array_types.update(array_types)

        self._append_initializer_array_types(array_types)

        if self._substances_spills is None:
            self._set_substancespills()

        # 'substance' data_array may have been added so initialize after
        # _set_substancespills() is invoked
        self.initialize_data_arrays()

    def _append_initializer_array_types(self, array_types):
        # for each array_types, use the key to get the associated initializer
        for key in array_types:
            for spill in self.spills:
                if spill.is_initializer(key):
                    self._array_types.update(
                        spill.get_initializer(key).array_types)

    def initialize_data_arrays(self):
        """
        initialize_data_arrays() is called without input data during rewind
        and prepare_for_model_run to define all data arrays.
        At this time the arrays are empty.
        """
        for name, atype in self._array_types.iteritems():
            # Initialize data_arrays with 0 elements
            if atype.shape is None:
                num_comp = self._oil_comp_array_len
                self._data_arrays[name] = \
                    atype.initialize_null(shape=(num_comp, ))
            else:
                self._data_arrays[name] = atype.initialize_null()

    def _append_data_arrays(self, num_released):
        """
        initialize data arrays once spill has spawned particles
        Data arrays are set to their initial_values

        :param int num_released: number of particles released

        """
        for name, atype in self._array_types.iteritems():
            # initialize all arrays even if 0 length
            if atype.shape is None:
                # assume array type is for weather data, provide it the shape
                # per the number of components used to model the oil
                # currently, we only have one type of oil, so all spills will
                # model same number of oil_components
                a_append = atype.initialize(num_released,
                                            shape=(self._oil_comp_array_len,),
                                            initial_value=tuple([0] * self._oil_comp_array_len))
            else:
                a_append = atype.initialize(num_released)
            self._data_arrays[name] = np.r_[self._data_arrays[name], a_append]

    def release_elements(self, time_step, model_time):
        """
        Called at the end of a time step

        This calls release_elements on all of the contained spills, and adds
        the elements to the data arrays

        :returns: total number of particles released

        todo: may need to update the 'mass' array to use a default of 1.0 but
        will need to define it in particle units or something along those lines
        """
        total_released = 0
        # substance index - used label elements from same substance
        # used internally only by SpillContainer - could be a strided array.
        # Simpler to define it only in SpillContainer as opposed to ArrayTypes
        # 'substance': ((), np.uint8, 0)
        for ix, spills in enumerate(self.iterspillsbysubstance()):
            num_rel_by_substance = 0
            for spill in spills:
                if not spill.on:
                    continue

                num_rel = spill.num_elements_to_release(model_time, time_step)
                if num_rel > 0:
                    # update 'spill_num' ArrayType's initial_value so it
                    # corresponds with spill number for this set of released
                    # particles - just another way to set value of spill_num
                    # correctly
                    self._array_types['spill_num'].initial_value = \
                        self.spills.index(spill)

                    if len(self['spill_num']) > 0:
                        # unique identifier for each new element released
                        # this adjusts the _array_types initial_value since the
                        # initialize function just calls:
                        #  range(initial_value, num_released + initial_value)
                        self._array_types['id'].initial_value = \
                            self['id'][-1] + 1
                    else:
                        # always reset value of first particle released to 0!
                        # The array_types are shared globally. To initialize
                        # uncertain spills correctly, reset this to 0.
                        # To be safe, always reset to 0 when no
                        # particles are released
                        self._array_types['id'].initial_value = 0

                    # append to data arrays - number of oil components is
                    # currently the same for all spills
                    self._append_data_arrays(num_rel)
                    spill.set_newparticle_values(num_rel,
                                                 model_time,
                                                 time_step,
                                                 self._data_arrays)
                    num_rel_by_substance += num_rel

            # always reset data arrays else the changing arrays are stale
            self._update_substance_array_reset_data(ix, num_rel_by_substance)

            # update total elements released for substance
            total_released += num_rel_by_substance

        return total_released

    def _reset_substances_spills_data(self, to_be_removed):
        'reset copies of data if elements are removed'
        if len(self.get_substances()) > 1:
            subs_idx = np.unique(self['substances'][to_be_removed])
            for ix in subs_idx:
                self._substances_spills.data[ix] = {}

    def model_step_is_done(self):
        '''
        Called at the end of a time step
        Need to remove particles marked as to_be_removed...
        '''
        if len(self._data_arrays) == 0:
            return  # nothing to do - arrays are not yet defined.

        to_be_removed = np.where(self['status_codes'] ==
                                 oil_status.to_be_removed)[0]

        if len(to_be_removed) > 0:
            for key in self._array_types.keys():
                self._reset_substances_spills_data(to_be_removed)
                self._data_arrays[key] = np.delete(self[key], to_be_removed,
                                                   axis=0)

    def __str__(self):
        return ('gnome.spill_container.SpillContainer\n'
                'spill LE attributes: {0}'
                .format(sorted(self._data_arrays.keys())))

    __repr__ = __str__


class SpillContainerPairData(object):
    """
    A really simple SpillContainerPair
      - holds SpillContainerPairData objects,
        but doen't manage spills, etc.

    Think of it as a read-only SpillContainerPair.

    Designed primarily to hold data retrieved from cache
    """
    def __init__(self, sc, u_sc=None):
        'Initialize object with the spill_containers passed in'
        if sc.uncertain:
            raise ValueError('sc is an uncertain SpillContainer')

        self._spill_container = sc

        if u_sc is None:
            self._uncertain = False
        else:
            self._uncertain = True

            if not u_sc.uncertain:
                raise ValueError('u_sc is not an uncertain SpillContainer')

            self._u_spill_container = u_sc

    def __repr__(self):
        return ('{0.__class__.__name__},\n'
                '  uncertain={0.uncertain}\n '.format(self))

    @property
    def uncertain(self):
        return self._uncertain

    def items(self):
        """
        returns a tuple of the enclosed spill containers

        if uncertainty is off, just one is in the tuple
        if uncertainly is on -- then it is a two-tuple:
            (certain_container, uncertain_container)

        To act on both:
            for sc in spill_container_pair.items():
                do_something_with(sc)
        """
        ## NOTE: cache code counts on the uncertain SpillContainer being last
        if self.uncertain:
            return (self._spill_container, self._u_spill_container)
        else:
            return (self._spill_container,)

    @property
    def LE_data(self):
        data = self._spill_container._data_arrays.keys()
        data.append('current_time_stamp')
        if self._spill_container.weathering_data:
            'only add if it is not an empty dict'
            data.append('weathering_data')

        return data

    def LE(self, prop_name, uncertain=False):
        if uncertain:
            sc = self._u_spill_container
        else:
            sc = self._spill_container

        if prop_name == 'current_time_stamp':
            return sc.current_time_stamp
        elif prop_name == 'weathering_data':
            return sc.weathering_data

        return sc[prop_name]

    def __eq__(self, other):
        'Compare equality of two SpillContainerPairData objects'
        if type(self) != type(other):
            return False

        if self.uncertain != other.uncertain:
            return False

        for sc in zip(self.items(), other.items()):
            if sc[0] != sc[1]:
                return False

        return True

    def __ne__(self, other):
        return not (self == other)


class SpillContainerPair(SpillContainerPairData):
    """
    Container holds two SpillContainers, one contains the certain spills while
    the other contains uncertainty spills if model uncertainty is on.
    """
    def __init__(self, uncertain=False):
        """
        initialize object:
        init spill_container, _uncertain and u_spill_container if uncertain

        Note: all operations like add, remove, replace and __iter__ are exposed
        to user for the spill_container.spills OrderedCollection
        """
        sc = SpillContainer()
        if uncertain:
            u_sc = SpillContainer(uncertain=True)
        else:
            u_sc = None

        super(SpillContainerPair, self).__init__(sc, u_sc)

    def rewind(self):
        'rewind spills in spill_container'
        self._spill_container.rewind()

        if self.uncertain:
            self._u_spill_container.rewind()

    def __repr__(self):
        'unambiguous repr'
        return ('{0.__class__.__name__},\n'
                '  uncertain={0.uncertain}\n'
                '  Spills: {1}'.format(self, self._spill_container.spills))

    @property
    def uncertain(self):
        return self._uncertain

    @uncertain.setter
    def uncertain(self, value):
        if type(value) is not bool:
            raise TypeError("uncertain property must be a bool (True/False)")

        if self._uncertain is True and value is False:
            self._uncertain = value
            del self._u_spill_container  # delete if it exists
            self.rewind()  # Not sure if we want to do this?
        elif self._uncertain is False and value is True:
            self._uncertain = value
            self._u_spill_container = self._spill_container.uncertain_copy()
            self.rewind()

    def _add_spill_pair(self, pair_tuple):
        'add both certain and uncertain spills given as a pair'
        if self.uncertain and len(pair_tuple) != 2:
            raise ValueError('You can only add a tuple containing a '
                             'certain/uncertain spill pair '
                             '(spill, uncertain_spill)')
        if not self.uncertain and len(pair_tuple) != 1:
            raise ValueError('Uncertainty is off. Tuple must only '
                             'contain (certain_spill,)')

        self._spill_container.spills += pair_tuple[0]
        if self.uncertain:
            self._u_spill_container.spills += pair_tuple[1]

    def _add_item(self, item):
        'could be a spill pair or a forecast spill - add appropriately'
        if isinstance(item, tuple):
            # add both certain and uncertain pair
            self._add_spill_pair(item)
        else:
            self._spill_container.spills += item
            if self.uncertain:
                self._u_spill_container.spills += item.uncertain_copy()

    def add(self, spills):
        """
        Add spill to spill_container and make copy in u_spill_container
        if uncertainty is on

        Note: Method can take either a list, tuple, or list of tuples with following
        assumptions:

        1. spills = Spill()    # A spill object, if uncertainty is on, make a
        copy for uncertain_spill_container.

        2. spills = [s0, s1, ..,]    # List of forecast spills. if uncertain,
        make a copy of each and add to uncertain_spill_container

        3. spills = (s0, uncertain_s0)    # tuple of length two. Assume first
        one is forecast spill and second one is the uncertain copy. Used
        when restoring from save file

        4. spills = [(s0, uncertain_s0), ..]    # list of tuples of length two.
        Added for completeness.
        """
        if isinstance(spills, list):
            for item in spills:
                self._add_item(item)
        else:
            # only adding one item, either a spill_pair or a forecast spill
            self._add_item(spills)

    def append(self, spill):
        self.add(spill)

    def remove(self, ident):
        '''
        remove object from spill_container.spills and the corresponding
        uncertainty spill as well
        '''
        if self.uncertain:
            'ident could be index or object so handle both'
            idx = self._spill_container.spills.index(
                self._spill_container.spills[ident])
            del self._u_spill_container.spills[idx]

        del self._spill_container.spills[ident]

    def __getitem__(self, ident):
        'only return the certain spill'
        spill = self._spill_container.spills[ident]
        return spill

    def __setitem__(self, ident, new_spill):
        self._spill_container.spills.replace(ident, new_spill)
        if self.uncertain:
            ix = self.index(new_spill)
            self._u_spill_container.spills[ix] = new_spill.uncertain_copy()

    def __delitem__(self, ident):
        self.remove(ident)

    def __iadd__(self, rop):
        self.add(rop)
        return self

    def __iter__(self):
        'iterates over the spills defined in spill_container'
        for sp in self._spill_container.spills:
            yield self.__getitem__(sp.id)

    def __len__(self):
        '''
        It refers to the total number of spills that have been added
        The uncertain and certain spill containers will contain the same number
        of spills return the length of spill_container.spills
        '''
        return len(self._spill_container.spills)

    def __contains__(self, ident):
        '''
        looks to see if ident which is the id of a spill belongs in the
        _spill_container.spills OrderedCollection
        '''
        return ident in self._spill_container.spills

    def to_dict(self):
        """
        takes the instance of SpillContainerPair class and outputs a dict with:
            'spills': call to_dict() on spills ordered collection
            stored in certain spill container

        if uncertain, then also return:
            'uncertain_spills': call to_dict() on spills ordered collection
            stored in uncertain spill container

        The input param json_ is not used. It is there to keep the same
        interface for all to_dict() functions
        """
        dict_ = {'spills':
                 self._spill_container.spills.to_dict()}
        if self.uncertain:
            dict_.update({'uncertain_spills':
                          self._u_spill_container.spills.to_dict()})
        return dict_

    def update_from_dict(self, dict_):
        '''
        takes a dict {'spills': [list of spill objects]}, checks them against
        the forecast spills contained in _spill_container.spills and updates
        if they are different

        It also creates a copy of the different spill and replaces the
        corresponding spill in _u_spill_container

        This is primarily intended for the webapp so the dict_ will only
        contain a list of forecast spills
        '''
        l_spills = dict_['spills']
        updated = False
        if len(l_spills) != len(self):
            updated = True

        if self._spill_container.spills.values() != l_spills:
            updated = True

        if updated:
            self.clear()
            if l_spills:
                self += l_spills
        return updated

    def spill_by_index(self, index, uncertain=False):
        '''return either the forecast spill or the uncertain spill at
        specified index'''
        if uncertain:
            return self._u_spill_container.spills[index]
        else:
            # __getitem__ should give correct result
            return self[index]

    def index(self, spill):
        '''
        Look for spill in forecast SpillContainer or uncertain SpillContainer
        and return the index of ordered collection where spill is found
        '''
        try:
            return self._spill_container.spills.index(spill)
        except:
            return self._u_spill_container.spills.index(spill)

    @property
    def num_released(self):
        'elements released by (forecast, uncertain) spills'
        if self.uncertain:
            return (self._spill_container.num_released,
                    self._u_spill_container.num_released)
        else:
            return (self._spill_container.num_released,)

    def clear(self):
        'clear all spills from container pairs'
        self._spill_container.spills.clear()
        if self.uncertain:
            self._u_spill_container.spills.clear()
