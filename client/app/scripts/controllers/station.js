'use strict';

/**
 * @ngdoc function
 * @name dcmetrometricsApp.controller:StationCtrl
 * @description
 * # StationCtrl
 * Controller of the dcmetrometricsApp
 */
angular.module('dcmetrometricsApp')

  .controller('StationCtrl', ['$scope', 'Page', '$stateParams', 'directory', 'statusTableUtils', 

     function ($scope, Page, $stateParams, directory, statusTableUtils) {

        $scope.statusTableUtils = statusTableUtils;
        $scope.stationName = $stateParams.station;

        $scope.escalators_have_station_descriptions = false;
        $scope.elevators_have_station_descriptions = false;

        $scope.no_escalators = false;
        $scope.no_elevators = false;

        // Request station directory data
        directory.get_directory().then( function(data) { 
          var i;

          $scope.stationDirectory = data.directory;
          $scope.stationData = data.shortNameToData[$scope.stationName];

          Page.title("DC Metro Metrics: " + $scope.stationData.long_name);
          Page.description("Escalator and elevator directory for " + $scope.stationData.long_name + " station " +
                           "in the WMATA Metrorail system in Washington, DC.");

          var escalators = $scope.stationData.escalators;
          for(i = 0; i < escalators.length; i++) {
            if (escalators[i].station_desc) {
              $scope.escalators_have_station_descriptions = true;
              break;
            }
          }

          var elevators = $scope.stationData.elevators;
          for(i = 0; i < elevators.length; i++) {
            if (elevators[i].station_desc) {
              $scope.elevators_have_station_descriptions = true;
              break;
            }
          }

          $scope.no_escalators = escalators.length === 0;
          $scope.no_elevators = elevators.length === 0;


        });

        $scope.getSymptomClass = function(unit) {

            var catToClass = {
              BROKEN : 'danger',
              INSPECTION : 'warning',
              OFF : 'danger',
              ON : 'success',
              REHAB : 'info'
            };
            var category = unit.key_statuses.lastStatus.symptom_category;
            return catToClass[category];

        };

        $scope.showEscalators = function() {
          var $state = $scope.$state;
          return $state.is('stations.detail.escalators') ||
            $state.is("stations.detail");
        };

        $scope.showElevators= function() {
          var $state = $scope.$state;
          return $state.is('stations.detail.elevators');
        };

        $scope.showRecentUpdates = function() {
          var $state = $scope.$state;
          return $state.is('stations.detail.recent');
        };

        
  }]);
