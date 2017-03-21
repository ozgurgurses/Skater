"""Partial Dependence class"""
from itertools import product, cycle
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from .base import BaseGlobalInterpretation
from ...util.static_types import StaticTypes
from ...util import exceptions

COLORS = ['#328BD5', '#404B5A', '#3EB642', '#E04341', '#8665D0']
plt.rcParams['figure.autolayout'] = True


class PartialDependence(BaseGlobalInterpretation):
    """Contains methods for partial dependence. Subclass of BaseGlobalInterpretation"""

    _pdp_metadata = {}
    _predict_fn = None

    @staticmethod
    def _build_fresh_metadata_dict():
        return {
            'pdp_cols': {},
            'sd_col':'',
            'val_cols':[]
        }

    def partial_dependence(self, feature_ids, predict_fn, grid=None, grid_resolution=100,
                           grid_range=None, sample=False,
                           sampling_strategy='uniform-over-similarity-ranks',
                           n_samples=5000, bin_count=50, samples_per_bin=10):

        """
        Computes partial_dependence of a set of variables. Essentially approximates
        the partial partial_dependence of the predict_fn with respect to the variables
        passed.

        Parameters:
        -----------
        feature_ids(list):
            the names/ids of the features for which we compute partial dependence.
            Note that the algorithm's complexity scales exponentially with additional
            features, so generally one should only look at one or two features at a
            time. These feature ids must be available in the class's associated DataSet.

            As of now, we only support looking at 1 or 2 features at a time.

        predict_fn(function):
            the machine learning model "prediction" function to explain, such that
            predictions = predict_fn(data).

            For instance:
            from sklearn.ensemble import RandomForestClassier
            rf = RandomForestClassier()
            rf.fit(X,y)

            partial_dependence(feature_ids, rf.predict)
            or
            partial_dependence(feature_ids, rf.predict_proba)

            are acceptable use cases. Output types need to be 1D or 2D numpy arrays.

            Supports classification, multi-class classification, and regression.

        grid(numpy.ndarray):
            2 dimensional array on which we fix values of features. Note this is
            determined automatically if not given based on the percentiles of the
            dataset.

        grid_resolution(int):
            how many unique values to include in the grid. If the percentile range
            is 5% to 95%, then that range will be cut into <grid_resolution>
            equally size bins.

        grid_range(tuple):
            the percentile extrama to consider. 2 element tuple, increasing, bounded
            between 0 and 1.

        sample(Bool):
            Whether to sample from the original dataset.

        sampling_strategy(string):
            If sampling, which approach to take. See DataSet.generate_sample for
            details.

        n_samples(int):
            The number of samples to use from the original dataset. Note this is
            only active if sample = True and sampling strategy = 'uniform'. If
            using 'uniform-over-similarity-ranks', use samples per bin

        bin_count(int):
            The number of bins to use when using the similarity based sampler. Note
            this is only active if sample = True and
            sampling_strategy = 'uniform-over-similarity-ranks'.
            total samples = bin_count * samples per bin.

        samples_per_bin(int):
            The number of samples to collect for each bin within the sampler. Note
            this is only active if sample = True and
            sampling_strategy = 'uniform-over-similarity-ranks'. If using
            sampling_strategy = 'uniform', use n_samples.
            total samples = bin_count * samples per bin.

        """

        if len(feature_ids) >= 3:
            too_many_features_err_msg = "Pass in at most 2 features for pdp. If you have a " \
                                        "use case where you'd like to look at 3 simultaneously" \
                                        ", please let us know."
            raise exceptions.TooManyFeaturesError(too_many_features_err_msg)

        if len(feature_ids) == 0:
            empty_features_err_msg = "Feature ids must have non-zero length"
            raise exceptions.EmptyFeatureListError(empty_features_err_msg)

        if len(set(feature_ids)) != len(feature_ids):
            duplicate_features_error_msg = "feature_ids cannot contain duplicate values"
            raise exceptions.DuplicateFeaturesError(duplicate_features_error_msg)

        if self.data_set is None:
            load_data_not_called_err_msg = "self.interpreter.data_set not found. " \
                                           "Please call Interpretation.load_data " \
                                           "before running this method."
            raise exceptions.DataSetNotLoadedError(load_data_not_called_err_msg)

        # TODO: This we can change easily to functional style
        missing_feature_ids = []
        for feature_id in feature_ids:
            if feature_id not in self.data_set.feature_ids:
                missing_feature_ids.append(feature_id)

        if missing_feature_ids:
            missing_feature_id_err_msg = "Features {0} not found in " \
                                         "Interpretation.data_set.feature_ids" \
                                         "{1}".format(missing_feature_ids, self.data_set.feature_ids)
            raise KeyError(missing_feature_id_err_msg)

        if grid_range is None:
            grid_range = (.03, 0.97)
        else:
            if not hasattr(grid_range, "__iter__"):
                err_msg = "Grid range {} needs to be an iterable".format(grid_range)
                raise exceptions.MalformedGridRangeError(err_msg)

        self._check_grid_range(grid_range)

        self._pdp_metadata = self._build_fresh_metadata_dict()

        # if you dont pass a grid, build one.
        grid = np.array(grid)
        if not grid.any():
            grid = self.data_set.generate_grid(feature_ids,
                                               grid_resolution=grid_resolution,
                                               grid_range=grid_range)
        else:
            if len(grid.shape) == 1:
                grid = grid[:, np.newaxis].T
                grid_resolution = grid.shape[1]

        self.interpreter.logger.debug("Grid shape used for pdp: {}".format(grid.shape))
        self.interpreter.logger.debug("Grid resolution for pdp: {}".format(grid_resolution))

        # make sure data_set module is giving us correct data structure
        self._check_grid(grid, feature_ids, grid_resolution)

        # generate data
        data_sample = self.data_set.generate_sample(strategy=sampling_strategy,
                                                    sample=sample,
                                                    n_samples_from_dataset=n_samples,
                                                    samples_per_bin=samples_per_bin,
                                                    bin_count=bin_count)

        self.interpreter.logger.debug("Shape of sampled data: {}".format(data_sample.shape))
        #TODO: Add check for non-empty data

        # make sure data_set module is giving us correct data structure
        self._check_dataset_type(data_sample)
        self._predict_fn = self.build_annotated_model(predict_fn, examples=data_sample)

        #cartesian product of grid
        grid_expanded = np.array(list(product(*grid)))

        # pandas dataframe
        data_sample_mutable = data_sample.copy()

        pdps = []

        if grid_expanded.shape[0] <= 0:
            empty_grid_expanded_err_msg = "Must have at least 1 pdp value" \
                                          "grid shape: {}".format(grid_expanded.shape)
            raise exceptions.MalformedGridError(empty_grid_expanded_err_msg)

        for i in range(grid_expanded.shape[0]):
            pdp = {}
            new_row = grid_expanded[i]
            for feature_idx, feature_id in enumerate(feature_ids):
                data_sample_mutable[feature_id] = new_row[feature_idx]

            predictions = self._predict_fn(data_sample_mutable.values)
            mean_prediction = np.mean(predictions, axis=0)
            std_prediction = np.std(predictions, axis=0)

            for feature_idx, feature_id in enumerate(feature_ids):
                val_col = 'val_{}'.format(feature_id)
                pdp[val_col] = new_row[feature_idx]

            # if predict_fn.n_classes not in (StaticTypes.unknown, StaticTypes.not_applicable):
            if isinstance(mean_prediction, np.ndarray):
                for class_i in range(mean_prediction.shape[0]):
                    mean_col = 'mean_class_{}'.format(class_i)
                    pdp[mean_col] = mean_prediction[class_i]
                    # we can return 1 sd since its a common variance across classes
                    # this line is currently redundant, as in it gets executed multiple times
                    pdp['sd'] = std_prediction[class_i]
            else:
                pdp['mean'] = mean_prediction
                pdp['sd'] = std_prediction

            pdps.append(pdp)

        self._pdp_metadata['val_cols'] = ['val_{}'.format(i) for i in feature_ids]

        # Local variable referenced possible before definition can be diregarded
        # since we assert that grid_expanded.shape must be > 0
        if isinstance(mean_prediction, np.ndarray):
            classes = range(mean_prediction.shape[0])
            self._pdp_metadata['pdp_cols'] = {
                class_i: "mean_class_{}".format(class_i) for class_i in classes
                }
        else:
            self._pdp_metadata['pdp_cols'] = {0:'mean'}

        self._pdp_metadata['sd_col'] = 'sd'

        self.interpreter.logger.debug("PDP df metadata: {}".format(self._pdp_metadata))
        return pd.DataFrame(pdps)

    def plot_partial_dependence(self, feature_ids, predict_fn, class_id=None,
                                grid=None, grid_resolution=100,
                                grid_range=None, sample=False,
                                sampling_strategy='uniform-over-similarity-ranks',
                                n_samples=5000, bin_count=50, samples_per_bin=10,
                                with_variance=False):

        """
        Computes partial_dependence of a set of variables. Essentially approximates
        the partial partial_dependence of the predict_fn with respect to the variables
        passed.

        Parameters:
        -----------
        feature_ids(list):
            the names/ids of the features for which we compute partial dependence.
            Note that the algorithm's complexity scales exponentially with additional
            features, so generally one should only look at one or two features at a
            time. These feature ids must be avaiable in the class's associated DataSet.

            As of now, we only support looking at 1 or 2 features at a time.

        predict_fn(function):
            machine learning that takes data and returns an output. Acceptable output
            formats are ????. Supports classification, multiclass classification,
            and regression.

        grid(numpy.ndarray):
            2 dimensional array on which we fix values of features. Note this is
            determined automatically if not given based on the percentiles of the
            dataset.

        grid_resolution(int):
            how many unique values to include in the grid. If the percentile range
            is 5% to 95%, then that range will be cut into <grid_resolution>
            equally size bins.

        grid_range(tuple):
            the percentile extrama to consider. 2 element tuple, increasing, bounded
            between 0 and 1.

        sample(Bool):
            Whether to sample from the original dataset.

        sampling_strategy(string):
            If sampling, which approach to take. See DataSet.generate_sample for
            details.

        n_samples(int):
            The number of samples to use from the original dataset. Note this is
            only active if sample = True and sampling strategy = 'uniform'. If
            using 'uniform-over-similarity-ranks', use samples per bin

        bin_count(int):
            The number of bins to use when using the similarity based sampler. Note
            this is only active if sample = True and
            sampling_strategy = 'uniform-over-similarity-ranks'.
            total samples = bin_count * samples per bin.

        samples_per_bin(int):
            The number of samples to collect for each bin within the sampler. Note
            this is only active if sample = True and
            sampling_strategy = 'uniform-over-similarity-ranks'. If using
            sampling_strategy = 'uniform', use n_samples.
            total samples = bin_count * samples per bin.

        with_variance(Bool):
            whether to include pdp error bars in the plots. Currently disabled for 3D
            plots for visibility. If you have a use case where you'd like error bars for
            3D pdp plots, let us know!

        plot_title(string):
            title for pdp plots

        """

        # in the event that a user wants a 3D pdp with multiple classes, how should
        # we handle this? currently each class will get its own figure

        pdp = self.partial_dependence(feature_ids, predict_fn,
                                      grid=grid, grid_resolution=grid_resolution,
                                      grid_range=grid_range, sample=sample,
                                      sampling_strategy=sampling_strategy,
                                      n_samples=n_samples, bin_count=bin_count,
                                      samples_per_bin=samples_per_bin)

        ax = self._plot_pdp_from_df(feature_ids, pdp, with_variance=with_variance)
        return ax

    def _plot_pdp_from_df(self, feature_ids, pdp, with_variance=False, plot_title=None):
        n_features = len(feature_ids)

        mean_columns = self._pdp_metadata['pdp_cols'].values()
        val_columns = self._pdp_metadata['val_cols']

        self.interpreter.logger.debug("Mean columns: {}".format(mean_columns))

        if n_features == 1:
            feature_name = val_columns[0]
            return self._2d_pdp_plot(pdp, feature_name, self._pdp_metadata,
                                     with_variance=with_variance,
                                     plot_title=plot_title)

        elif n_features == 2:
            feature1, feature2 = val_columns
            return self._3d_pdp_plot(pdp, feature1, feature2, self._pdp_metadata,
                                     with_variance=with_variance,
                                     plot_title=plot_title)

    def _2d_pdp_plot(self, pdp, feature_name, pdp_metadata,
                     with_variance=False, plot_title=None):
        colors = cycle(COLORS)
        figure_list, axis_list = [], []

        class_col_pairs = pdp_metadata['pdp_cols'].items()
        sd_col = pdp_metadata['sd_col']

        for class_name, mean_col in class_col_pairs:

            # if class_name is None:
            #     raise ValueError("Could not parse class name from {}".format(mean_col))

            f, ax = plt.subplots(1)
            figure_list.append(f)
            axis_list.append(ax)
            color = colors.next()

            data = pdp.set_index(feature_name)
            plane = data[mean_col]
            plane.plot(ax=ax, color=color)

            if with_variance:
                upper_plane = plane + data[sd_col]
                lower_plane = plane - data[sd_col]
                ax.fill_between(data.index.values,
                                lower_plane.values,
                                upper_plane.values,
                                alpha=.2,
                                color=color)
            if plot_title:
                ax.set_title(plot_title)
            ax.set_ylabel('Predicted {}'.format(class_name))
            ax.set_xlabel(feature_name)
            handles, labels = ax.get_legend_handles_labels()
            ax.legend(handles, labels)
        return figure_list, axis_list

    def _3d_pdp_plot(self, pdp, feature1, feature2, pdp_metadata,
                     with_variance=False, plot_title=None):
        colors = cycle(COLORS)
        figure_list, axis_list = [], []

        class_col_pairs = pdp_metadata['pdp_cols'].items()
        sd_col = pdp_metadata['sd_col']

        for class_name, mean_col in class_col_pairs:
            f = plt.figure()
            ax = f.add_subplot(111, projection='3d')
            if plot_title:
                ax.set_title("Partial Dependence")
            figure_list.append(f)
            axis_list.append(ax)
            color = colors.next()
            ax.plot_trisurf(pdp[feature1].values, pdp[feature2].values,
                            pdp[mean_col].values, alpha=.5, color=color)
            if with_variance:
                var_color = colors.next()
                ax.plot_trisurf(pdp[feature1].values, pdp[feature2].values,
                                (pdp[mean_col] + pdp[sd_col]).values, alpha=.2,
                                color=var_color)
                ax.plot_trisurf(pdp[feature1].values, pdp[feature2].values,
                                (pdp[mean_col] - pdp[sd_col]).values, alpha=.2,
                                color=var_color)
            ax.set_xlabel(feature1)
            ax.set_ylabel(feature2)

            ax.set_zlabel("Predicted {}".format(class_name))
            handles, labels = ax.get_legend_handles_labels()
            ax.legend(handles, labels)

        return figure_list, axis_list

    def partial_dependency_sklearn(self):
        """Uses sklearn's implementation"""
        raise NotImplementedError("Not yet included")

    @staticmethod
    def _check_grid(grid, feature_ids, grid_resolution):

        if not isinstance(grid, np.ndarray):
            err_msg = "Grid of type {} must be a numpy array".format(type(grid))
            raise exceptions.MalformedGridError(err_msg)
        if len(grid.shape) != 2:
            err_msg = "Grid of shape {} not 2D".format(grid.shape)
            raise exceptions.MalformedGridError(grid)

        if len(feature_ids) != grid.shape[0]:
            err_msg = "Given {0} features, there must be {1} rows in grid" \
                      "but {2} were found".format(len(feature_ids),
                                                  len(feature_ids),
                                                  grid.shape[0])
            raise exceptions.MalformedGridError(err_msg)

        if grid_resolution != grid.shape[1]:
            err_msg = "Given {0} grid resolution, there must be {1} columns in grid" \
                      "but {2} were found".format(grid_resolution,
                                                  grid_resolution,
                                                  grid.shape[1])
            raise exceptions.MalformedGridError(err_msg)

    @staticmethod
    def _check_dataset_type(dataset):
        """
        Ensures that dataset is pandas dataframe
        :param dataset:
        :return:
        """
        if not isinstance(dataset, pd.DataFrame):
            err_msg = "Dataset.data must be a pandas.dataframe"
            raise exceptions.DataSetError(err_msg)

    @staticmethod
    def _check_grid_range(grid_range):
        """
        Tested by unit test, ensures grid range is between 0 and 1
        :param grid_range (tuple)

        """
        if len(grid_range) != 2:
            err_msg = "Grid range {} must have 2 elements".format(grid_range)
            raise exceptions.MalformedGridRangeError(err_msg)
        if not all([i >= 0 and i <= 1 for i in grid_range]):
            err_msg = "All elements of grid range {} " \
                      "must be between 0 and 1".format(grid_range)
            raise exceptions.MalformedGridRangeError(err_msg)
