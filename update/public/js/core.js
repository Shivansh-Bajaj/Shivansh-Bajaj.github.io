var angular = require('angular');

var app=angular.module('myapp', [
    require('angular-typer')
]);
app.controller('mycontroller', function($scope) {
  var vm = this;
  $scope.variable=10; 
  vm.words = ['Pizza', 'Chips', 'Salad!'];
  vm.typeCount = 0;
  vm.deleteCount = 0;

  vm.onType = function() {
    vm.typeCount++;
  };

  vm.onDelete = function() {
    vm.deleteCount++;
  }

});
